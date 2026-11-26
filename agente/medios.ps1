# PulsePC - lo que suena ahora en Windows (Spotify, navegador, reproductor...).
# Lo arranca el agente (oculto). Escribe una linea JSON por segundo en la salida:
#   {"titulo":"...","artista":"...","app":"Spotify.exe","estado":"reproduciendo",
#    "posicion":83.4,"duracion":215.0}   (segundos; sin posicion/duracion si la app no las da)
# o {} si no hay ninguna sesion multimedia. Termina solo cuando se cierra la salida.
# Usa la API de WinRT GlobalSystemMediaTransportControlsSessionManager (Windows 10 1809+).
param([int]$IntervaloMs = 1000)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Vigilancia del agente: en Windows PowerShell escribir en la salida NO falla aunque el
# agente se haya cerrado (la tuberia no avisa), asi que el script quedaria vivo para
# siempre. Por eso se apunta quien lo lanzo y se termina en cuanto ese proceso desaparece.
$padre = $null
try {
    $idPadre = (Get-CimInstance Win32_Process -Filter "ProcessId=$PID").ParentProcessId
    $padre = [System.Diagnostics.Process]::GetProcessById([int]$idPadre)
} catch { $padre = $null }

function AgenteVivo {
    if ($padre -eq $null) { return $true }
    try { $padre.Refresh(); return -not $padre.HasExited } catch { return $false }
}

function Escribir($obj) {
    $linea = if ($obj -eq $null) { '{}' } else { $obj | ConvertTo-Json -Compress }
    [Console]::Out.WriteLine($linea)
    [Console]::Out.Flush()
}

try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
    $null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties, Windows.Media.Control, ContentType = WindowsRuntime]
    $null = [Windows.Storage.Streams.IRandomAccessStreamWithContentType, Windows.Storage.Streams, ContentType = WindowsRuntime]
    $null = [Windows.Storage.Streams.IInputStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
    # El flujo de la miniatura llega como objeto COM y PowerShell no sabe pasarlo a
    # AsStreamForRead(IInputStream) directamente: se llama por reflexion, que si lo convierte.
    $aStream = [System.IO.WindowsRuntimeStreamExtensions].GetMethod('AsStreamForRead',
        [Type[]]@([Windows.Storage.Streams.IInputStream]))

    # Las llamadas WinRT son asincronas: se convierten en Task de .NET y se espera el resultado.
    $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1

    function Esperar($op, [Type]$tipo) {
        $t = $asTask.MakeGenericMethod($tipo).Invoke($null, @($op))
        if (-not $t.Wait(3000)) { return $null }
        return $t.Result
    }

    $gestor = Esperar ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) `
        ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
    if ($gestor -eq $null) { throw 'no responde el gestor de sesiones multimedia' }
} catch {
    try { Escribir @{ error = $_.Exception.Message } } catch { }
    exit 1
}

$estados = @{
    'Playing' = 'reproduciendo'; 'Paused' = 'pausa'; 'Stopped' = 'detenido'
    'Changing' = 'cambiando'; 'Opened' = 'abierto'; 'Closed' = 'cerrado'
}

# --- Portada ---
# Se guarda en un archivo solo cuando cambia la cancion. "portada_id" cambia con cada
# cancion (hash de titulo + artista + app) para que el movil sepa cuando pedirla otra vez.
$rutaPortada = Join-Path $env:TEMP 'pulsopc_portada.jpg'
$md5 = [System.Security.Cryptography.MD5]::Create()
$portada = @{ id = ''; ruta = ''; intentos = 0 }

# Reloj propio de la cancion (ver "Tiempo de la cancion" mas abajo).
$reloj = @{ id = ''; est = ''; firma = ''; p0 = -1.0; lejos = 0; base = 0.0; sw = [System.Diagnostics.Stopwatch]::StartNew() }

function IdCancion($titulo, $artista, $app) {
    if (-not $titulo -and -not $artista) { return '' }
    $b = [System.Text.Encoding]::UTF8.GetBytes("$titulo|$artista|$app")
    return (($md5.ComputeHash($b) | ForEach-Object { $_.ToString('x2') }) -join '').Substring(0, 12)
}

function GuardarPortada($props) {
    if ($props -eq $null -or $props.Thumbnail -eq $null) { return $false }
    $flujo = Esperar ($props.Thumbnail.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
    if ($flujo -eq $null) { return $false }
    $tmp = "$rutaPortada.tmp"
    try {
        $entrada = $aStream.Invoke($null, @($flujo))
        $archivo = [System.IO.File]::Create($tmp)
        try { $entrada.CopyTo($archivo) } finally { $archivo.Close(); $entrada.Dispose() }
        if ((Get-Item $tmp).Length -eq 0) { return $false }
        # Se escribe aparte y luego se reemplaza: el agente nunca lee una imagen a medias.
        Move-Item -LiteralPath $tmp -Destination $rutaPortada -Force
        return $true
    } catch {
        return $false
    } finally {
        # Es un objeto COM (no tiene Dispose): se libera asi.
        try { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($flujo) } catch { }
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
}

while (AgenteVivo) {
    $dato = $null
    try {
        $s = $gestor.GetCurrentSession()
        if ($s -ne $null) {
            $p = Esperar ($s.TryGetMediaPropertiesAsync()) `
                ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
            $est = [string]$s.GetPlaybackInfo().PlaybackStatus
            $app = [string]$s.SourceAppUserModelId
            # "Spotify.exe" o "308046B0AF4A39CB" (id de app): nos quedamos con algo legible
            if ($app -match '!') { $app = ($app -split '!')[-1] }
            $titulo = if ($p) { [string]$p.Title } else { '' }
            $artista = if ($p) { [string]$p.Artist } else { '' }
            $id = IdCancion $titulo $artista $app
            if ($id -ne $portada.id) {
                $portada.id = $id; $portada.ruta = ''; $portada.intentos = 0
            }
            # La miniatura a veces llega bastante despues que el titulo (en Spotify, al pasar
            # de cancion, mas de 5 s): se reintenta una vez por segundo durante 20 s.
            if ($id -and -not $portada.ruta -and $portada.intentos -lt 20) {
                $portada.intentos++
                if (GuardarPortada $p) { $portada.ruta = $rutaPortada }
            }
            # Tiempo de la cancion/video. Lo que da Windows va a tirones: Spotify y los
            # navegadores actualizan la posicion solo a ratos, y LastUpdatedTime no siempre se
            # actualiza a la vez, asi que "Position + lo que ha pasado desde LastUpdatedTime"
            # unas veces se queda atras y otras se pasa. Por eso se lleva un reloj propio que
            # avanza solo mientras suena, y Windows solo lo corrige cuando trae datos nuevos que
            # se alejan mas de 5 s (salto en la cancion), o al cambiar de cancion o de estado.
            $pos = $null; $dur = $null
            try {
                $tl = $s.GetTimelineProperties()
                $d = ($tl.EndTime - $tl.StartTime).TotalSeconds
                if ($d -gt 0) {
                    $p0 = ($tl.Position - $tl.StartTime).TotalSeconds
                    $firma = "$($tl.Position.Ticks)|$($tl.LastUpdatedTime.UtcTicks)"
                    $nuevo = $firma -ne $reloj.firma
                    $x = $p0
                    if ($est -eq 'Playing') {
                        $pasado = 0.0
                        if ($tl.LastUpdatedTime.Year -gt 2000) {
                            $pasado = ([DateTimeOffset]::Now - $tl.LastUpdatedTime).TotalSeconds
                            if ($pasado -lt 0 -or $pasado -gt 86400) { $pasado = 0.0 }
                        }
                        # Si la posicion acaba de cambiar (se lee cada segundo), es de hace menos
                        # de 1 s aunque LastUpdatedTime diga otra cosa.
                        if ($reloj.id -eq $id -and $p0 -ne $reloj.p0) { $pasado = [Math]::Min($pasado, 1.0) }
                        $x += $pasado
                    }
                    $x = [Math]::Min([Math]::Max($x, 0), $d)
                    $prev = $reloj.base
                    if ($reloj.est -eq 'Playing') { $prev += $reloj.sw.Elapsed.TotalSeconds }
                    $mismo = $reloj.id -eq $id -and $reloj.est -eq $est
                    # Un dato suelto que se aleja no basta (Windows a veces manda uno malo): hace
                    # falta que dos lecturas nuevas seguidas se alejen mas de 5 s.
                    $lejos = $nuevo -and [Math]::Abs($x - $prev) -ge 5
                    if ($lejos) { $reloj.lejos++ } elseif ($nuevo) { $reloj.lejos = 0 }
                    if ($mismo -and $reloj.lejos -lt 2) {
                        $x = [Math]::Min($prev, $d)   # se sigue el reloj propio
                    } else {
                        $reloj.lejos = 0
                        $reloj.base = $x
                        $reloj.sw.Restart()
                    }
                    $reloj.id = $id; $reloj.est = $est; $reloj.firma = $firma; $reloj.p0 = $p0
                    $dur = [Math]::Round($d, 1)
                    $pos = [Math]::Round($x, 1)
                }
            } catch { $pos = $null; $dur = $null }
            $dato = [ordered]@{
                titulo       = $titulo
                artista      = $artista
                app          = $app
                estado       = if ($estados.ContainsKey($est)) { $estados[$est] } else { $est.ToLower() }
                portada_ruta = $portada.ruta
                portada_id   = if ($portada.ruta) { $portada.id } else { '' }
            }
            if ($dur -ne $null) { $dato['posicion'] = $pos; $dato['duracion'] = $dur }
        }
    } catch {
        $dato = $null   # la sesion puede cerrarse a mitad de lectura
    }
    try {
        Escribir $dato
    } catch {
        break   # el agente se ha cerrado
    }
    Start-Sleep -Milliseconds $IntervaloMs
}
