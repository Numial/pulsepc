# PulsoPC - lector de sensores con LibreHardwareMonitorLib.
# Lo arranca el agente (oculto). Escribe una linea JSON por segundo en la salida:
#   {"sensores":[{"hw":"Intel Core i7","tipo_hw":"Cpu","nombre":"CPU Package","tipo":"Temperature","valor":54.0}, ...]}
# Para leer la temperatura de la CPU hace falta ejecutarlo como administrador.
param(
    [Parameter(Mandatory = $true)][string]$Dir,
    [int]$IntervaloMs = 1000
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

try {
    # Sus dependencias (HidSharp, etc.) se resuelven solas desde la misma carpeta.
    [void][System.Reflection.Assembly]::LoadFrom((Join-Path $Dir 'LibreHardwareMonitorLib.dll'))
    $pc = New-Object LibreHardwareMonitor.Hardware.Computer
    $pc.IsCpuEnabled = $true
    $pc.IsGpuEnabled = $true
    $pc.IsMotherboardEnabled = $true
    $pc.IsStorageEnabled = $true
    $pc.IsMemoryEnabled = $true
    $pc.IsControllerEnabled = $true
    $pc.Open()
} catch {
    [Console]::Out.WriteLine((@{ error = $_.Exception.Message } | ConvertTo-Json -Compress))
    exit 1
}

$tipos = @('Temperature', 'Fan', 'Power', 'Clock', 'Load', 'Control')

function Leer($hw, $lista) {
    $hw.Update()
    foreach ($s in $hw.Sensors) {
        $t = [string]$s.SensorType
        if ($tipos -contains $t -and $s.Value -ne $null) {
            [void]$lista.Add([ordered]@{
                hw      = $hw.Name
                tipo_hw = [string]$hw.HardwareType
                nombre  = $s.Name
                tipo    = $t
                valor   = [math]::Round([double]$s.Value, 1)
            })
        }
    }
    foreach ($sub in $hw.SubHardware) { Leer $sub $lista }
}

while ($true) {
    $lista = New-Object System.Collections.ArrayList
    foreach ($hw in $pc.Hardware) {
        try { Leer $hw $lista } catch { }
    }
    $linea = @{ sensores = $lista.ToArray() } | ConvertTo-Json -Compress -Depth 4
    try {
        [Console]::Out.WriteLine($linea)
        [Console]::Out.Flush()
    } catch {
        break   # el agente se ha cerrado
    }
    Start-Sleep -Milliseconds $IntervaloMs
}
$pc.Close()
