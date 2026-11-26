package com.pulsopc.app;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.view.View;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.Locale;

/**
 * Panel dibujado a mano con Canvas (sin librerias), para que vaya fluido
 * en moviles antiguos. Las paginas, widgets y colores vienen del "diseno"
 * que se edita en el PC (http://localhost:47801/editor) y llega con cada /stats.
 */
public class PanelView extends View {

    // Colores del tema (se cambian desde el editor del PC). Aviso y alerta son fijos.
    private int FONDO = 0xFF0E1116, TARJETA = 0xFF171B22, PISTA = 0x22E6E6E6;
    private int TEXTO = 0xFFE6E6E6, TENUE = 0x99E6E6E6;
    private int ACENTO = 0xFF3EC7A0, SECUNDARIO = 0xFF5B9BF0;
    /** Color de los numeros/valores y segundo color del degradado de las tarjetas. */
    private int NUMEROS = 0xFFE6E6E6, TARJETA2 = 0xFF232A35;
    /** Degradado de las tarjetas: "no", "vertical", "horizontal" o "diagonal". */
    private String degradado = "no";
    static final int AVISO = 0xFFF2B33D, ALERTA = 0xFFF0645B;

    /** Pagina por defecto si el agente es antiguo y no manda diseno. */
    private static final String DISENO_BASE = "{\"cabecera\":true,\"paginas\":["
            + "{\"columnas\":3,\"filas\":2,\"widgets\":[{\"tipo\":\"cpu\"},{\"tipo\":\"ram\"},{\"tipo\":\"gpu\"},"
            + "{\"tipo\":\"temps\"},{\"tipo\":\"red\"},{\"tipo\":\"disco\"}]},"
            + "{\"columnas\":3,\"filas\":1,\"widgets\":[{\"tipo\":\"nucleos\"},{\"tipo\":\"gpu_detalle\"},{\"tipo\":\"ventiladores\"}]}]}";

    private JSONArray paginas;
    private boolean cabecera = true;
    private long versionDiseno = -1;
    private String tituloActual = "";

    private static final int N = 60; // muestras de historico (1 por segundo)

    private final float dp;
    private final Paint pRelleno = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint pTrazo = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint pTexto = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint pImagen = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.FILTER_BITMAP_FLAG);
    private final RectF r = new RectF();
    private final Path ruta = new Path();

    private JSONObject s;
    /** Cuando llegaron los datos. */
    private long recibido;
    /** Reloj propio de la cancion: posicion en relojMs, y que cancion/estado era. */
    private double relojPos;
    private long relojMs;
    private String relojId = "";

    /**
     * El PC ya manda un tiempo suave; aqui solo se evita que el retraso de la red entre
     * lecturas lo haga ir hacia atras: si el dato nuevo va por delante se toma, si va un
     * poco por detras se frena poco a poco, y con 5 s o mas (salto) se va ahi directamente.
     */
    private void relojCancion(JSONObject m) {
        if (m == null || m.optDouble("duracion", 0) <= 0) { relojId = ""; return; }
        boolean suena = "reproduciendo".equals(m.optString("estado", ""));
        String id = m.optString("titulo", "") + "|" + m.optString("artista", "") + "|" + suena;
        double nueva = m.optDouble("posicion", 0);
        long ahora = System.currentTimeMillis();
        double prevista = relojPos + (suena ? (ahora - relojMs) / 1000.0 : 0);
        double dif = nueva - prevista;
        if (!id.equals(relojId) || Math.abs(dif) >= 5) prevista = nueva;   // otra cancion o salto
        else if (dif > 0) prevista = nueva;                                // el PC va por delante
        else prevista += Math.max(dif, -0.25);                             // se frena sin retroceder de golpe
        relojPos = prevista;
        relojMs = ahora;
        relojId = id;
    }
    private String aviso = T.t("Iniciando...", "Starting...");
    private long ultimaRespuesta;

    private final float[] histCpu = new float[N], histGpu = new float[N];
    private final float[] histBajada = new float[N], histSubida = new float[N];
    private final float[] histRam = new float[N], histTempCpu = new float[N], histTempGpu = new float[N];
    private int alertaTemp = 90;
    private JSONObject widgetActual;

    /** Botones tocables (multimedia) dibujados en el ultimo fotograma. */
    private final java.util.ArrayList<RectF> zonas = new java.util.ArrayList<>();
    private final java.util.ArrayList<String> acciones = new java.util.ArrayList<>();
    private String pulsado;
    private long pulsadoHasta;

    private android.graphics.Bitmap portada;
    public void setPortada(android.graphics.Bitmap b) { portada = b; invalidate(); }

    // Widget "imagen": fotos (Bitmap) y GIF animados (Movie), descargados por MainActivity.
    private final java.util.HashMap<String, android.graphics.Bitmap> fotos = new java.util.HashMap<>();
    private final java.util.HashMap<String, android.graphics.Movie> gifs = new java.util.HashMap<>();
    private final java.util.HashMap<String, android.graphics.Bitmap> fotogramas = new java.util.HashMap<>();
    private final long inicioGif = System.currentTimeMillis();
    private final android.graphics.Rect origen = new android.graphics.Rect();

    public void setImagen(String id, android.graphics.Bitmap foto, android.graphics.Movie gif) {
        if (gif != null) gifs.put(id, gif); else if (foto != null) fotos.put(id, foto);
        invalidate();
    }

    /** Imagenes que usa el diseno actual (las que hay que descargar). */
    public java.util.ArrayList<String> imagenesDelDiseno() {
        java.util.ArrayList<String> ids = new java.util.ArrayList<>();
        if (paginas == null) return ids;
        for (int i = 0; i < paginas.length(); i++) {
            JSONObject p = paginas.optJSONObject(i);
            JSONArray ws = p != null ? p.optJSONArray("widgets") : null;
            if (ws == null) continue;
            for (int j = 0; j < ws.length(); j++) {
                JSONObject wd = ws.optJSONObject(j);
                String id = wd != null && "imagen".equals(wd.optString("tipo")) ? wd.optString("imagen", "") : "";
                if (!id.isEmpty() && !ids.contains(id)) ids.add(id);
            }
        }
        return ids;
    }

    public boolean tieneImagen(String id) { return fotos.containsKey(id) || gifs.containsKey(id); }

    public interface OyenteAccion { void onAccion(String accion); }
    private OyenteAccion oyente;
    public void setOyenteAccion(OyenteAccion o) { oyente = o; }
    private int cabeza;
    private int pagina;
    private String conexion = "";

    public void setConexion(String c) { conexion = c; }

    public void siguientePagina() {
        int n = paginas != null ? paginas.length() : 1;
        pagina = (pagina + 1) % Math.max(1, n);
        invalidate();
    }

    public PanelView(Context c) {
        super(c);
        dp = c.getResources().getDisplayMetrics().density;
        pTrazo.setStyle(Paint.Style.STROKE);
        pTrazo.setStrokeCap(Paint.Cap.ROUND);
        pTexto.setTypeface(Typeface.create("sans-serif-condensed", Typeface.NORMAL));
        setLongClickable(true);
        try { aplicarDiseno(new JSONObject(DISENO_BASE)); } catch (Exception ignorado) { }
    }

    private static int color(JSONObject t, String k, int defecto) {
        try {
            String v = t.optString(k, "");
            if (v.length() == 7 && v.charAt(0) == '#') return 0xFF000000 | Integer.parseInt(v.substring(1), 16);
        } catch (Exception ignorado) { }
        return defecto;
    }

    private void aplicarDiseno(JSONObject d) {
        JSONObject t = d.optJSONObject("tema");
        if (t != null) {
            FONDO = color(t, "fondo", FONDO);
            TARJETA = color(t, "tarjeta", TARJETA);
            ACENTO = color(t, "acento", ACENTO);
            SECUNDARIO = color(t, "secundario", SECUNDARIO);
            TEXTO = color(t, "texto", TEXTO);
            NUMEROS = color(t, "numeros", TEXTO);
            TARJETA2 = color(t, "tarjeta2", TARJETA2);
            TENUE = (TEXTO & 0x00FFFFFF) | 0x99000000;
            PISTA = (TEXTO & 0x00FFFFFF) | 0x22000000;
        }
        cabecera = d.optBoolean("cabecera", true);
        degradado = d.optString("degradado", "no");
        pTexto.setTypeface(tipografia(d.optString("fuente", "condensada")));
        alertaTemp = d.optInt("alerta_temp", 90);
        JSONArray p = d.optJSONArray("paginas");
        if (p != null && p.length() > 0) paginas = p;
        if (paginas != null && pagina >= paginas.length()) pagina = 0;
    }

    /** Tipografias del sistema que tiene Android 6 (sin meter archivos de fuentes). */
    private static Typeface tipografia(String f) {
        switch (f) {
            case "moderna": return Typeface.create("sans-serif", Typeface.NORMAL);
            case "fina": return Typeface.create("sans-serif-light", Typeface.NORMAL);
            case "gruesa": return Typeface.create("sans-serif-black", Typeface.NORMAL);
            case "serif": return Typeface.create("serif", Typeface.NORMAL);
            case "mono": return Typeface.create("monospace", Typeface.NORMAL);
            case "manuscrita": return Typeface.create("cursive", Typeface.NORMAL);
            case "informal": return Typeface.create("casual", Typeface.NORMAL);
            default: return Typeface.create("sans-serif-condensed", Typeface.NORMAL);
        }
    }

    /** Sin datos durante mas de 30 s: el PC esta apagado o el agente cerrado. */
    public boolean pcApagado() {
        return s != null && System.currentTimeMillis() - ultimaRespuesta > 30000;
    }

    public boolean conectado() {
        return s != null && System.currentTimeMillis() - ultimaRespuesta < 5000;
    }

    public void actualizar(JSONObject datos) {
        s = datos;
        recibido = System.currentTimeMillis();
        relojCancion(datos.optJSONObject("medios"));
        aviso = null;
        ultimaRespuesta = System.currentTimeMillis();
        JSONObject cpu = datos.optJSONObject("cpu");
        JSONObject red = datos.optJSONObject("red");
        JSONObject gpu = primeraGpu();
        cabeza = (cabeza + 1) % N;
        histCpu[cabeza] = cpu != null ? (float) cpu.optDouble("uso", 0) : 0;
        histGpu[cabeza] = gpu != null ? (float) gpu.optDouble("uso", 0) : 0;
        histBajada[cabeza] = red != null ? (float) red.optDouble("bajada_kbs", 0) : 0;
        histSubida[cabeza] = red != null ? (float) red.optDouble("subida_kbs", 0) : 0;
        JSONObject ram = datos.optJSONObject("ram");
        histRam[cabeza] = ram != null ? (float) ram.optDouble("porcentaje", 0) : 0;
        histTempCpu[cabeza] = cpu != null && !cpu.isNull("temp") ? (float) cpu.optDouble("temp", 0) : 0;
        histTempGpu[cabeza] = gpu != null ? (float) gpu.optDouble("temp", 0) : 0;
        String idioma = datos.optString("idioma", "");
        if (!idioma.isEmpty()) T.en = "en".equals(idioma);
        JSONObject d = datos.optJSONObject("diseno");
        if (d != null && d.optLong("version", 0) != versionDiseno) {
            versionDiseno = d.optLong("version", 0);
            aplicarDiseno(d);   // cambio hecho en el editor del PC: se aplica al momento
        }
        invalidate();
    }

    public void aviso(String texto) {
        aviso = texto;
        invalidate();
    }

    private JSONObject primeraGpu() {
        if (s == null) return null;
        JSONArray g = s.optJSONArray("gpu");
        return g != null && g.length() > 0 ? g.optJSONObject(0) : null;
    }

    // ----------------------------------------------------------------- dibujo --

    @Override
    protected void onDraw(Canvas c) {
        int w = getWidth(), h = getHeight();
        c.drawColor(FONDO);
        zonas.clear();
        acciones.clear();
        // Anti-quemado: la pantalla esta encendida dia y noche, asi que todo el
        // contenido se desplaza un par de pixeles cada minuto (no se nota a simple vista).
        int paso = (int) ((System.currentTimeMillis() / 60000) % 4);
        float desp = 2 * dp;
        c.translate(paso == 1 || paso == 2 ? desp : 0, paso >= 2 ? desp : 0);
        w -= (int) desp;
        h -= (int) desp;
        float m = 8 * dp;
        boolean sinDatos = s == null || pcApagado();
        boolean conCab = cabecera || sinDatos;
        float alturaCab = conCab ? 28 * dp : 0;

        if (conCab && !pcApagado()) dibujarCabecera(c, m, m, w - 2 * m, alturaCab);

        if (pcApagado()) {
            // PC apagado: pantalla casi negra con un texto discreto que cambia de sitio.
            pTexto.setColor((TEXTO & 0x00FFFFFF) | 0x55000000);
            pTexto.setTextSize(14 * dp);
            pTexto.setTextAlign(Paint.Align.CENTER);
            long t = System.currentTimeMillis() / 30000;
            float px = w * (0.3f + 0.4f * ((t * 37) % 10) / 10f);
            float py = h * (0.3f + 0.4f * ((t * 53) % 10) / 10f);
            c.drawText(T.t("PulsePC · esperando al PC", "PulsePC · waiting for the PC"), px, py, pTexto);
            return;
        }

        if (s == null) {
            pTexto.setColor(TENUE);
            pTexto.setTextSize(16 * dp);
            pTexto.setTextAlign(Paint.Align.CENTER);
            String[] lineas = (aviso == null ? "" : aviso).split("\n");
            for (int i = 0; i < lineas.length; i++)
                c.drawText(lineas[i], w / 2f, h / 2f + i * 22 * dp, pTexto);
            return;
        }

        float top = conCab ? m + alturaCab + m : m;
        int n = paginas != null ? paginas.length() : 0;
        if (n == 0) return;
        if (pagina >= n) pagina = 0;
        dibujarPagina(c, paginas.optJSONObject(pagina), w, h, top, m, w < h, n > 1);
        // Puntos de pagina
        if (n > 1) {
            for (int i = 0; i < n; i++) {
                pRelleno.setColor(i == pagina ? TEXTO : PISTA);
                c.drawCircle(w / 2f + (i - (n - 1) / 2f) * 12 * dp, h - 4 * dp, 2.5f * dp, pRelleno);
            }
        }
    }

    /**
     * Coloca los widgets en la rejilla igual que el editor del PC: en orden,
     * en el primer hueco donde quepan. En vertical se gira la rejilla.
     */
    private void dibujarPagina(Canvas c, JSONObject p, int w, int h, float top, float m,
                               boolean girar, boolean conPuntos) {
        if (p == null) return;
        int cols = Math.max(1, Math.min(4, p.optInt("columnas", 3)));
        int filas = Math.max(1, Math.min(4, p.optInt("filas", 2)));
        if (girar && cols > filas) { int t = cols; cols = filas; filas = t; } else girar = false;
        JSONArray ws = p.optJSONArray("widgets");
        if (ws == null) return;
        boolean[][] ocupado = new boolean[filas][cols];
        float pie = conPuntos ? 6 * dp : 0;
        float cw = (w - m * (cols + 1)) / cols;
        float ch = (h - top - m * filas - pie) / filas;
        for (int i = 0; i < ws.length(); i++) {
            JSONObject wd = ws.optJSONObject(i);
            if (wd == null) continue;
            int an = wd.optInt("ancho", 1), al = wd.optInt("alto", 1);
            if (girar) { int t = an; an = al; al = t; }
            an = Math.max(1, Math.min(cols, an));
            al = Math.max(1, Math.min(filas, al));
            int ff = -1, cc = -1;
            buscar:
            for (int f = 0; f <= filas - al; f++) {
                for (int col = 0; col <= cols - an; col++) {
                    boolean libre = true;
                    for (int y = f; y < f + al && libre; y++)
                        for (int x = col; x < col + an; x++)
                            if (ocupado[y][x]) { libre = false; break; }
                    if (libre) { ff = f; cc = col; break buscar; }
                }
            }
            if (ff < 0) continue; // no cabe
            for (int y = ff; y < ff + al; y++)
                for (int x = cc; x < cc + an; x++) ocupado[y][x] = true;
            float x = m + cc * (cw + m), y = top + ff * (ch + m);
            float tw = cw * an + m * (an - 1), th = ch * al + m * (al - 1);
            r.set(x, y, x + tw, y + th);
            pRelleno.setColor(TARJETA);
            android.graphics.LinearGradient deg = null;
            if (!"no".equals(degradado)) {
                float x2 = "vertical".equals(degradado) ? x : x + tw;
                float y2 = "horizontal".equals(degradado) ? y : y + th;
                deg = new android.graphics.LinearGradient(x, y, x2, y2, TARJETA, TARJETA2,
                        android.graphics.Shader.TileMode.CLAMP);
                pRelleno.setShader(deg);
            }
            c.drawRoundRect(r, 10 * dp, 10 * dp, pRelleno);
            pRelleno.setShader(null);
            float pd = 10 * dp;
            tituloActual = wd.optString("titulo", "");
            widgetActual = wd;
            String tipo = wd.optString("tipo", "");
            if ("imagen".equals(tipo)) tarjetaImagen(c, wd, x, y, tw, th);
            else dibujarWidget(c, tipo, x + pd, y + pd, tw - 2 * pd, th - 2 * pd);
            // Alerta: borde rojo parpadeante si la temperatura se dispara
            if (enAlerta(tipo) && (System.currentTimeMillis() / 1000) % 2 == 0) {
                pTrazo.setColor(ALERTA);
                pTrazo.setStrokeWidth(3 * dp);
                r.set(x + 1.5f * dp, y + 1.5f * dp, x + tw - 1.5f * dp, y + th - 1.5f * dp);
                c.drawRoundRect(r, 10 * dp, 10 * dp, pTrazo);
            }
        }
        tituloActual = "";
    }

    private void dibujarWidget(Canvas c, String tipo, float ix, float iy, float iw, float ih) {
        switch (tipo) {
            case "cpu": tarjetaCpu(c, ix, iy, iw, ih); break;
            case "ram": tarjetaRam(c, ix, iy, iw, ih); break;
            case "gpu": tarjetaGpu(c, ix, iy, iw, ih); break;
            case "temps": tarjetaTemps(c, ix, iy, iw, ih); break;
            case "red": tarjetaRed(c, ix, iy, iw, ih); break;
            case "disco": tarjetaDisco(c, ix, iy, iw, ih); break;
            case "nucleos": tarjetaNucleos(c, ix, iy, iw, ih); break;
            case "gpu_detalle": tarjetaGpuDetalle(c, ix, iy, iw, ih); break;
            case "ventiladores": tarjetaVentiladores(c, ix, iy, iw, ih); break;
            case "reloj": tarjetaReloj(c, ix, iy, iw, ih); break;
            case "procesos": tarjetaProcesos(c, ix, iy, iw, ih); break;
            case "bateria": tarjetaBateria(c, ix, iy, iw, ih); break;
            case "multimedia": tarjetaMultimedia(c, ix, iy, iw, ih); break;
            case "grafica": tarjetaGrafica(c, ix, iy, iw, ih); break;
        }
    }

    /** Foto o GIF ocupando toda la tarjeta (recortada o entera, segun el ajuste). */
    private void tarjetaImagen(Canvas c, JSONObject wd, float x, float y, float w, float h) {
        String id = wd.optString("imagen", "");
        android.graphics.Bitmap bmp = fotos.get(id);
        android.graphics.Movie gif = gifs.get(id);
        if (gif != null && gif.width() > 0 && gif.height() > 0) {
            // Movie se pinta en un lienzo propio (por software) y luego se escala como una foto.
            bmp = fotogramas.get(id);
            if (bmp == null) {
                bmp = android.graphics.Bitmap.createBitmap(gif.width(), gif.height(), android.graphics.Bitmap.Config.ARGB_8888);
                fotogramas.put(id, bmp);
            }
            int dur = gif.duration() > 0 ? gif.duration() : 1000;
            gif.setTime((int) ((System.currentTimeMillis() - inicioGif) % dur));
            bmp.eraseColor(0);
            gif.draw(new Canvas(bmp), 0, 0);
            postInvalidateDelayed(66);   // siguiente fotograma (~15 por segundo)
        }
        if (bmp == null) {
            String t = id.isEmpty() ? T.t("Elige una foto en el editor", "Choose a photo in the editor") : T.t("Cargando imagen...", "Loading image...");
            pTexto.setColor(TENUE);
            pTexto.setTextSize(12 * dp);
            pTexto.setTextAlign(Paint.Align.CENTER);
            c.drawText(encajar(t, w - 16 * dp), x + w / 2, y + h / 2, pTexto);
            return;
        }
        float bw = bmp.getWidth(), bh = bmp.getHeight();
        c.save();
        r.set(x, y, x + w, y + h);
        ruta.reset();
        ruta.addRoundRect(r, 10 * dp, 10 * dp, Path.Direction.CW);
        c.clipPath(ruta);
        if ("contener".equals(wd.optString("ajuste"))) {
            float e = Math.min(w / bw, h / bh);
            float dw = bw * e, dh = bh * e;
            r.set(x + (w - dw) / 2, y + (h - dh) / 2, x + (w + dw) / 2, y + (h + dh) / 2);
            c.drawBitmap(bmp, null, r, pImagen);
        } else {
            float e = Math.max(w / bw, h / bh);
            float sw = w / e, sh = h / e;
            origen.set((int) ((bw - sw) / 2), (int) ((bh - sh) / 2), (int) ((bw + sw) / 2), (int) ((bh + sh) / 2));
            c.drawBitmap(bmp, origen, r, pImagen);
        }
        String t = wd.optString("titulo", "");
        if (!t.isEmpty()) {
            pTexto.setColor(0xFFFFFFFF);
            pTexto.setTextSize(13 * dp);
            pTexto.setTextAlign(Paint.Align.LEFT);
            pTexto.setShadowLayer(4 * dp, 0, 1 * dp, 0xCC000000);
            c.drawText(encajar(t, w - 20 * dp), x + 10 * dp, y + h - 10 * dp, pTexto);
            pTexto.clearShadowLayer();
        }
        c.restore();
    }

    private float tempCpu() {
        JSONObject cpu = s.optJSONObject("cpu");
        return cpu != null && !cpu.isNull("temp") ? (float) cpu.optDouble("temp", 0) : 0;
    }

    private float tempGpu() {
        JSONObject g = primeraGpu();
        return g != null ? (float) g.optDouble("temp", 0) : 0;
    }

    private boolean enAlerta(String tipo) {
        switch (tipo) {
            case "cpu": return tempCpu() >= alertaTemp;
            case "gpu": case "gpu_detalle": return tempGpu() >= alertaTemp;
            case "temps": return Math.max(tempCpu(), tempGpu()) >= alertaTemp;
            case "grafica": {
                String m = widgetActual != null ? widgetActual.optString("metrica", "cpu") : "cpu";
                return ("temp_cpu".equals(m) && tempCpu() >= alertaTemp)
                        || ("temp_gpu".equals(m) && tempGpu() >= alertaTemp);
            }
            default: return false;
        }
    }

    private void textoCentrado(Canvas c, String t, float x, float y, float w) {
        pTexto.setTextAlign(Paint.Align.CENTER);
        pTexto.setColor(TENUE);
        pTexto.setTextSize(13 * dp);
        c.drawText(encajar(t, w), x + w / 2, y, pTexto);
    }

    private void tarjetaProcesos(Canvas c, float x, float y, float w, float h) {
        titulo(c, T.t("Procesos", "Processes"), x, y, w);
        JSONArray p = s.optJSONArray("procesos");
        if (p == null || p.length() == 0) {
            textoCentrado(c, T.t("Calculando...", "Calculating..."), x, y + h / 2, w);
            return;
        }
        float fila = Math.max(22 * dp, Math.min(30 * dp, (h - 22 * dp) / Math.max(1, p.length())));
        float yy = y + 22 * dp;
        float max = 1;
        for (int i = 0; i < p.length(); i++) max = Math.max(max, (float) p.optJSONObject(i).optDouble("cpu", 0));
        for (int i = 0; i < p.length() && yy + fila <= y + h + 6 * dp; i++) {
            JSONObject o = p.optJSONObject(i);
            float cpu = (float) o.optDouble("cpu", 0);
            String valor = String.format(Locale.ROOT, "%.1f %%", cpu);
            pTexto.setTextSize(12 * dp);
            float anchoV = pTexto.measureText(valor);
            pTexto.setTextAlign(Paint.Align.RIGHT);
            pTexto.setColor(NUMEROS);
            c.drawText(valor, x + w, yy + 12 * dp, pTexto);
            pTexto.setTextAlign(Paint.Align.LEFT);
            pTexto.setColor(TEXTO);
            c.drawText(encajar(o.optString("nombre", "?"), w - anchoV - 8 * dp), x, yy + 12 * dp, pTexto);
            barra(c, x, yy + 16 * dp, w, 3 * dp, 100 * cpu / max, colorPorNivel(cpu, 25, 50));
            yy += fila;
        }
    }

    private void tarjetaBateria(Canvas c, float x, float y, float w, float h) {
        titulo(c, T.t("Batería", "Battery"), x, y, w);
        JSONObject b = s.optJSONObject("bateria");
        if (b == null) {
            textoCentrado(c, T.t("Este PC no tiene batería", "This PC has no battery"), x, y + h / 2, w);
            return;
        }
        int pct = b.optInt("porcentaje", 0);
        boolean cargando = b.optBoolean("cargando", false);
        // Pila dibujada
        float bw = Math.min(w * 0.7f, (h - 50 * dp) * 2.2f), bh = bw / 2.2f;
        float bx = x + (w - bw) / 2 - 3 * dp, by = y + 22 * dp + (h - 22 * dp - bh - 24 * dp) / 2;
        pTrazo.setColor(TENUE);
        pTrazo.setStrokeWidth(2 * dp);
        r.set(bx, by, bx + bw, by + bh);
        c.drawRoundRect(r, 6 * dp, 6 * dp, pTrazo);
        pRelleno.setColor(TENUE);
        r.set(bx + bw, by + bh * 0.3f, bx + bw + 5 * dp, by + bh * 0.7f);
        c.drawRoundRect(r, 2 * dp, 2 * dp, pRelleno);
        int col = cargando ? ACENTO : pct <= 15 ? ALERTA : pct <= 30 ? AVISO : ACENTO;
        pRelleno.setColor(col);
        float in = 4 * dp;
        r.set(bx + in, by + in, bx + in + (bw - 2 * in) * pct / 100f, by + bh - in);
        c.drawRoundRect(r, 3 * dp, 3 * dp, pRelleno);
        pTexto.setTextAlign(Paint.Align.CENTER);
        pTexto.setColor(NUMEROS);
        pTexto.setTextSize(bh * 0.45f);
        c.drawText(pct + "%", bx + bw / 2, by + bh / 2 + bh * 0.16f, pTexto);
        String estado;
        if (cargando) estado = pct >= 100 ? T.t("Cargada · enchufada", "Charged · plugged in") : T.t("Cargando", "Charging");
        else if (!b.isNull("minutos")) {
            int min = b.optInt("minutos", 0);
            estado = (min / 60) + " h " + (min % 60) + T.t(" min restantes", " min left");
        } else estado = T.t("Con batería", "On battery");
        pTexto.setColor(TENUE);
        pTexto.setTextSize(12 * dp);
        c.drawText(encajar(estado, w), x + w / 2, by + bh + 20 * dp, pTexto);
    }

    private void tarjetaMultimedia(Canvas c, float x, float y, float w, float h) {
        titulo(c, "Multimedia", x, y, w);
        JSONObject m = s.optJSONObject("medios");
        String tituloM = m != null ? m.optString("titulo", "") : "";
        String artista = m != null ? m.optString("artista", "") : "";
        boolean sonando = m != null && "reproduciendo".equals(m.optString("estado", ""));
        float top = y + 24 * dp;
        if (!tituloM.isEmpty()) {
            // Portada a la izquierda (si hay espacio), titulo y artista a su lado
            float lado = Math.min(Math.max(44 * dp, h * 0.32f), w * 0.4f);
            // El hueco de la portada se reserva siempre: si no, al llegar la portada (a veces
            // unos segundos despues que el titulo) los botones saltaban y se pulsaba en vacio.
            r.set(x, top, x + lado, top + lado);
            if (portada != null) {
                c.save();
                ruta.reset();
                ruta.addRoundRect(r, 6 * dp, 6 * dp, Path.Direction.CW);
                c.clipPath(ruta);
                c.drawBitmap(portada, null, r, pImagen);
                c.restore();
            } else {
                pRelleno.setColor(PISTA);
                c.drawRoundRect(r, 6 * dp, 6 * dp, pRelleno);
                notaMusical(c, x + lado / 2, top + lado / 2, lado * 0.18f);
            }
            float tx = x + lado + 10 * dp;
            float tw = x + w - tx;
            float cy = top + lado / 2 - 8 * dp;
            pTexto.setTextAlign(Paint.Align.LEFT);
            pTexto.setColor(TEXTO);
            pTexto.setTextSize(16 * dp);
            c.drawText(encajar(tituloM, tw), tx, cy + 8 * dp, pTexto);
            pTexto.setColor(TENUE);
            pTexto.setTextSize(12 * dp);
            String sub = artista;
            String app = m.optString("app", "");
            if (!app.isEmpty()) sub = sub.isEmpty() ? app : sub + "  ·  " + app;
            c.drawText(encajar(sub, tw), tx, cy + 26 * dp, pTexto);
            top += lado + 8 * dp;
            double dur = m.optDouble("duracion", 0);
            if (dur > 0) top = tiempoCancion(c, m, dur, sonando, x, top, w);
        }
        // Dos filas de botones: anterior / play-pausa / siguiente y volumen - / silencio / +
        float zonaH = y + h - top;
        float filaH = Math.min(zonaH / 2f, w / 3f);
        float b = Math.min(filaH - 6 * dp, w / 3f - 8 * dp);
        String[][] filas = {{"anterior", "play", "siguiente"}, {"vol_menos", "silencio", "vol_mas"}};
        for (int f = 0; f < 2; f++) {
            float cy = top + filaH * f + filaH / 2;
            for (int i = 0; i < 3; i++) {
                float cx = x + w * (i + 0.5f) / 3f;
                String acc = filas[f][i];
                boolean activo = acc.equals(pulsado) && System.currentTimeMillis() < pulsadoHasta;
                r.set(cx - b / 2, cy - b / 2 * (f == 0 ? 1f : 0.8f), cx + b / 2, cy + b / 2 * (f == 0 ? 1f : 0.8f));
                pRelleno.setColor(activo ? ACENTO : PISTA);
                c.drawRoundRect(r, b / 2, b / 2, pRelleno);
                zonas.add(new RectF(r));
                acciones.add(acc);
                icono(c, acc, cx, cy, b * 0.22f, activo ? FONDO : TEXTO, sonando);
            }
        }
    }

    /**
     * Barra de progreso con "1:23" a la izquierda y "3:45" a la derecha. Entre una lectura y
     * otra la posicion se adelanta aqui si esta sonando, asi el tiempo avanza segundo a segundo.
     */
    private float tiempoCancion(Canvas c, JSONObject m, double dur, boolean sonando, float x, float top, float w) {
        double pos = relojPos;
        if (sonando) pos += (System.currentTimeMillis() - relojMs) / 1000.0;
        pos = Math.max(0, Math.min(pos, dur));
        float alto = 4 * dp;
        r.set(x, top, x + w, top + alto);
        pRelleno.setColor(PISTA);
        c.drawRoundRect(r, alto / 2, alto / 2, pRelleno);
        r.set(x, top, x + Math.max(alto, (float) (w * pos / dur)), top + alto);
        pRelleno.setColor(ACENTO);
        c.drawRoundRect(r, alto / 2, alto / 2, pRelleno);
        pTexto.setColor(TENUE);
        pTexto.setTextSize(11 * dp);
        float ty = top + alto + 13 * dp;
        pTexto.setTextAlign(Paint.Align.LEFT);
        c.drawText(minSeg(pos), x, ty, pTexto);
        pTexto.setTextAlign(Paint.Align.RIGHT);
        c.drawText(minSeg(dur), x + w, ty, pTexto);
        pTexto.setTextAlign(Paint.Align.LEFT);
        // Se vuelve a pintar justo al cambiar de segundo (solo mientras suena).
        if (sonando) postInvalidateDelayed(1000 - (long) ((pos * 1000) % 1000));
        return ty + 8 * dp;
    }

    /** 83 -> "1:23"; 3725 -> "1:02:05". */
    private static String minSeg(double segundos) {
        int t = (int) segundos;
        int h = t / 3600, mi = (t / 60) % 60, se = t % 60;
        String ss = (se < 10 ? "0" : "") + se;
        return h > 0 ? h + ":" + (mi < 10 ? "0" : "") + mi + ":" + ss : mi + ":" + ss;
    }

    /** Nota musical tenue para el hueco de la portada mientras no hay imagen. */
    private void notaMusical(Canvas c, float cx, float cy, float t) {
        pRelleno.setColor(TENUE);
        pTrazo.setColor(TENUE);
        pTrazo.setStrokeWidth(Math.max(2 * dp, t * 0.22f));
        c.drawCircle(cx - t * 0.5f, cy + t * 0.9f, t * 0.55f, pRelleno);
        c.drawLine(cx, cy + t * 0.9f, cx, cy - t * 1.3f, pTrazo);
        c.drawLine(cx, cy - t * 1.3f, cx + t * 0.8f, cy - t * 0.8f, pTrazo);
    }

    /** Iconos dibujados con formas (sin depender de fuentes con emojis, que en Android 6 fallan). */
    private void icono(Canvas c, String acc, float cx, float cy, float t, int color, boolean sonando) {
        pRelleno.setColor(color);
        pTrazo.setColor(color);
        pTrazo.setStrokeWidth(Math.max(2 * dp, t * 0.25f));
        ruta.reset();
        switch (acc) {
            case "play":
                if (sonando) {   // pausa
                    c.drawRect(cx - t * 0.8f, cy - t, cx - t * 0.25f, cy + t, pRelleno);
                    c.drawRect(cx + t * 0.25f, cy - t, cx + t * 0.8f, cy + t, pRelleno);
                } else {
                    ruta.moveTo(cx - t * 0.6f, cy - t); ruta.lineTo(cx + t, cy); ruta.lineTo(cx - t * 0.6f, cy + t);
                    ruta.close(); c.drawPath(ruta, pRelleno);
                }
                break;
            case "siguiente":
            case "anterior": {
                float d = "siguiente".equals(acc) ? 1 : -1;
                ruta.moveTo(cx - d * t, cy - t * 0.8f); ruta.lineTo(cx + d * t * 0.5f, cy); ruta.lineTo(cx - d * t, cy + t * 0.8f);
                ruta.close(); c.drawPath(ruta, pRelleno);
                float bx = cx + d * t * 0.6f;
                c.drawRect(Math.min(bx, bx + d * t * 0.3f), cy - t * 0.8f, Math.max(bx, bx + d * t * 0.3f), cy + t * 0.8f, pRelleno);
                break;
            }
            default: {   // altavoz
                float ox = cx - t * 0.4f;
                ruta.moveTo(ox - t * 0.6f, cy - t * 0.35f); ruta.lineTo(ox - t * 0.15f, cy - t * 0.35f);
                ruta.lineTo(ox + t * 0.4f, cy - t * 0.85f); ruta.lineTo(ox + t * 0.4f, cy + t * 0.85f);
                ruta.lineTo(ox - t * 0.15f, cy + t * 0.35f); ruta.lineTo(ox - t * 0.6f, cy + t * 0.35f);
                ruta.close(); c.drawPath(ruta, pRelleno);
                float sx = ox + t * 0.8f;
                if ("silencio".equals(acc)) {
                    c.drawLine(sx, cy - t * 0.4f, sx + t * 0.8f, cy + t * 0.4f, pTrazo);
                    c.drawLine(sx, cy + t * 0.4f, sx + t * 0.8f, cy - t * 0.4f, pTrazo);
                } else {
                    c.drawLine(sx, cy, sx + t * 0.9f, cy, pTrazo);
                    if ("vol_mas".equals(acc)) c.drawLine(sx + t * 0.45f, cy - t * 0.45f, sx + t * 0.45f, cy + t * 0.45f, pTrazo);
                }
            }
        }
    }

    private void tarjetaGrafica(Canvas c, float x, float y, float w, float h) {
        String m = widgetActual != null ? widgetActual.optString("metrica", "cpu") : "cpu";
        String nombre, valor;
        float[] hist;
        float max;
        int color = ACENTO;
        switch (m) {
            case "gpu": nombre = "GPU"; hist = histGpu; max = 100; valor = Math.round(hist[cabeza]) + " %"; color = SECUNDARIO; break;
            case "ram": nombre = T.t("Memoria", "Memory"); hist = histRam; max = 100; valor = Math.round(hist[cabeza]) + " %"; break;
            case "temp_cpu": nombre = T.t("Temperatura CPU", "CPU temperature"); hist = histTempCpu; max = 100; valor = Math.round(hist[cabeza]) + " °C"; color = colorPorNivel(hist[cabeza], 75, alertaTemp); break;
            case "temp_gpu": nombre = T.t("Temperatura GPU", "GPU temperature"); hist = histTempGpu; max = 100; valor = Math.round(hist[cabeza]) + " °C"; color = colorPorNivel(hist[cabeza], 75, alertaTemp); break;
            case "red": nombre = T.t("Red (bajada)", "Network (download)"); hist = histBajada; max = 0; valor = velocidad(hist[cabeza]); break;
            default: nombre = "CPU"; hist = histCpu; max = 100; valor = Math.round(hist[cabeza]) + " %";
        }
        // Titulo y valor en la misma linea si caben; si no (tarjeta estrecha), el valor baja
        // a su propia linea en vez de pisar el titulo.
        float tamV = Math.min(28 * dp, h * 0.18f);
        pTexto.setTextSize(tamV);
        float anchoV = pTexto.measureText(valor);
        pTexto.setTextSize(12 * dp);
        String tit = (tituloActual != null && !tituloActual.isEmpty() ? tituloActual : nombre).toUpperCase(Locale.ROOT);
        boolean juntos = pTexto.measureText(tit) + anchoV + 10 * dp <= w;
        titulo(c, nombre, x, y, juntos ? w - anchoV - 10 * dp : w);
        pTexto.setTextAlign(Paint.Align.RIGHT);
        pTexto.setColor(NUMEROS);
        pTexto.setTextSize(tamV);
        float baseV = juntos ? y + tamV : y + 16 * dp + tamV;
        c.drawText(encajar(valor, w), x + w, baseV, pTexto);
        float gy = baseV + 10 * dp;
        float gh = y + h - gy;
        // Relleno suave bajo la linea
        if (max <= 0) { for (float v : hist) max = Math.max(max, v); if (max <= 0) max = 1; }
        ruta.reset();
        ruta.moveTo(x, gy + gh);
        for (int i = 0; i < N; i++) {
            float v = hist[(cabeza + 1 + i) % N];
            ruta.lineTo(x + w * i / (N - 1), gy + gh - gh * Math.min(v / max, 1f));
        }
        ruta.lineTo(x + w, gy + gh);
        ruta.close();
        pRelleno.setColor((color & 0x00FFFFFF) | 0x33000000);
        c.drawPath(ruta, pRelleno);
        linea(c, hist, x, gy, w, gh, max, color);
        pTexto.setTextAlign(Paint.Align.LEFT);
        pTexto.setColor(TENUE);
        pTexto.setTextSize(10 * dp);
        c.drawText(T.t("últimos 60 s", "last 60 s"), x, y + h, pTexto);
    }

    @Override
    public boolean onTouchEvent(android.view.MotionEvent e) {
        int a = e.getActionMasked();
        if (a == android.view.MotionEvent.ACTION_DOWN || a == android.view.MotionEvent.ACTION_UP) {
            // Ojo: el lienzo esta desplazado por el anti-quemado; 2 dp no cambian el acierto.
            for (int i = 0; i < zonas.size(); i++) {
                RectF z = zonas.get(i);
                if (z.contains(e.getX(), e.getY())) {
                    if (a == android.view.MotionEvent.ACTION_UP) {
                        pulsado = acciones.get(i);
                        pulsadoHasta = System.currentTimeMillis() + 300;
                        if (oyente != null) oyente.onAccion(pulsado);
                        invalidate();
                        postInvalidateDelayed(320);
                    }
                    return true;
                }
            }
        }
        return super.onTouchEvent(e);
    }

    private void tarjetaReloj(Canvas c, float x, float y, float w, float h) {
        titulo(c, T.t("Reloj", "Clock"), x, y, w);
        java.util.Date ahora = new java.util.Date();
        String hora = new java.text.SimpleDateFormat("HH:mm", Locale.getDefault()).format(ahora);
        String fecha = new java.text.SimpleDateFormat(T.en ? "EEEE, MMMM d" : "EEEE d MMMM", T.locale()).format(ahora);
        pTexto.setTextAlign(Paint.Align.CENTER);
        pTexto.setColor(NUMEROS);
        float tam = Math.min(h * 0.5f, w / 2.6f);
        pTexto.setTextSize(tam);
        hora = encajar(hora, w);
        tam = pTexto.getTextSize();
        float tamFecha = Math.max(11 * dp, tam * 0.2f);
        // Hora y fecha centradas juntas, con la fecha siempre debajo sin pisar la hora.
        float bloque = tam * 0.72f + tamFecha * 1.6f;
        float base = y + 16 * dp + (h - 16 * dp - bloque) / 2 + tam * 0.72f;
        c.drawText(hora, x + w / 2, base, pTexto);
        pTexto.setColor(TENUE);
        pTexto.setTextSize(tamFecha);
        c.drawText(encajar(fecha, w), x + w / 2, base + tamFecha * 1.6f, pTexto);
    }

    private void dibujarCabecera(Canvas c, float x, float y, float w, float h) {
        boolean ok = conectado();
        pRelleno.setColor(ok ? ACENTO : ALERTA);
        c.drawCircle(x + 6 * dp, y + h / 2, 5 * dp, pRelleno);
        pTexto.setTextAlign(Paint.Align.LEFT);
        pTexto.setColor(TEXTO);
        pTexto.setTextSize(16 * dp);
        String equipo = s != null ? s.optString("equipo", "PC") : "PulsePC";
        equipo = encajar(equipo, w * 0.5f);
        float finEquipo = x + 18 * dp + pTexto.measureText(equipo);
        c.drawText(equipo, x + 18 * dp, y + h / 2 + 6 * dp, pTexto);
        if (s != null) {
            pTexto.setTextAlign(Paint.Align.RIGHT);
            pTexto.setColor(TENUE);
            pTexto.setTextSize(12 * dp);
            String extra = (conexion.isEmpty() ? "" : conexion + "  ·  ") + s.optString("so", "")
                    + T.t("  ·  encendido ", "  ·  up ") + duracion(s.optDouble("uptime_s", 0));
            if (!ok) extra = T.t("sin conexión  ·  ", "offline  ·  ") + extra;
            c.drawText(encajar(extra, x + w - finEquipo - 12 * dp), x + w, y + h / 2 + 5 * dp, pTexto);
        }
    }

    private void titulo(Canvas c, String t, float x, float y, float w) {
        if (tituloActual != null && !tituloActual.isEmpty()) t = tituloActual;
        pTexto.setTextAlign(Paint.Align.LEFT);
        pTexto.setColor(TENUE);
        pTexto.setTextSize(12 * dp);
        c.drawText(encajar(t.toUpperCase(Locale.ROOT), w), x, y + 12 * dp, pTexto);
    }

    /**
     * Hace que el texto quepa en maxW con el tamano actual de pTexto: primero lo
     * reduce (hasta un 75 %) y, si aun no cabe, lo recorta con "...". Asi nada se
     * sale de su tarjeta aunque el widget sea estrecho (p. ej. 3 columnas en vertical).
     */
    private String encajar(String t, float maxW) {
        if (t == null) return "";
        float ancho = pTexto.measureText(t);
        if (ancho <= maxW || maxW <= 0) return t;
        float tam = pTexto.getTextSize();
        pTexto.setTextSize(Math.max(tam * 0.75f, tam * maxW / ancho));
        if (pTexto.measureText(t) <= maxW) return t;
        int n = pTexto.breakText(t, true, Math.max(0, maxW - pTexto.measureText("…")), null);
        return t.substring(0, Math.max(0, n)) + "…";
    }

    /** Anillo de porcentaje con el valor en el centro. */
    private void anillo(Canvas c, float cx, float cy, float radio, float pct, String centro, String sub) {
        float grosor = Math.max(6 * dp, radio * 0.16f);
        pTrazo.setStrokeWidth(grosor);
        r.set(cx - radio, cy - radio, cx + radio, cy + radio);
        pTrazo.setColor(PISTA);
        c.drawArc(r, 135, 270, false, pTrazo);
        pTrazo.setColor(colorPorNivel(pct, 70, 90));
        c.drawArc(r, 135, 270 * Math.max(0, Math.min(100, pct)) / 100f, false, pTrazo);
        pTexto.setTextAlign(Paint.Align.CENTER);
        pTexto.setColor(NUMEROS);
        pTexto.setTextSize(radio * 0.5f);
        c.drawText(encajar(centro, radio * 1.6f), cx, cy + radio * 0.17f, pTexto);
        if (sub != null) {
            pTexto.setColor(TENUE);
            pTexto.setTextSize(Math.max(10 * dp, radio * 0.2f));
            // A esa altura el anillo esta a +-0.8 radios del centro: se deja margen para no tocarlo.
            // Si no cabe entero (p. ej. "57 °C  ·  1.62 GHz" con letra ancha), se queda
            // solo la primera parte en vez de cortarla a medias.
            if (pTexto.measureText(sub) > radio * 1.3f && sub.contains("  ·  ")) sub = sub.substring(0, sub.indexOf("  ·  "));
            c.drawText(encajar(sub, radio * 1.3f), cx, cy + radio * 0.6f, pTexto);
        }
    }

    /** Grafica de linea del historico, escalada a max (o al maximo visto si max<=0). */
    private void linea(Canvas c, float[] hist, float x, float y, float w, float h, float max, int color) {
        if (max <= 0) {
            for (float v : hist) max = Math.max(max, v);
            if (max <= 0) max = 1;
        }
        ruta.reset();
        for (int i = 0; i < N; i++) {
            float v = hist[(cabeza + 1 + i) % N];
            float px = x + w * i / (N - 1);
            float py = y + h - h * Math.min(v / max, 1f);
            if (i == 0) ruta.moveTo(px, py); else ruta.lineTo(px, py);
        }
        pTrazo.setStrokeWidth(2 * dp);
        pTrazo.setColor(color);
        c.drawPath(ruta, pTrazo);
    }

    private void barra(Canvas c, float x, float y, float w, float h, float pct, int color) {
        r.set(x, y, x + w, y + h);
        pRelleno.setColor(PISTA);
        c.drawRoundRect(r, h / 2, h / 2, pRelleno);
        r.set(x, y, x + w * Math.max(0, Math.min(100, pct)) / 100f, y + h);
        pRelleno.setColor(color);
        c.drawRoundRect(r, h / 2, h / 2, pRelleno);
    }

    private void tarjetaCpu(Canvas c, float x, float y, float w, float h) {
        JSONObject cpu = s.optJSONObject("cpu");
        if (cpu == null) return;
        titulo(c, "CPU", x, y, w);
        float uso = (float) cpu.optDouble("uso", 0);
        double f = cpu.optDouble("freq_mhz", Double.NaN);
        double tc = cpu.optDouble("temp", Double.NaN);
        String sub = Double.isNaN(f) ? null : String.format(Locale.ROOT, "%.2f GHz", f / 1000);
        if (!Double.isNaN(tc)) sub = Math.round(tc) + " °C" + (sub != null ? "  ·  " + sub : "");
        float grafH = h * 0.22f;
        float radio = Math.min(w, h - grafH - 20 * dp) / 2f * 0.85f;
        anillo(c, x + w / 2, y + 16 * dp + (h - grafH - 16 * dp) / 2, radio, uso,
                Math.round(uso) + "%", sub);
        linea(c, histCpu, x, y + h - grafH, w, grafH, 100, ACENTO);
    }

    private void tarjetaRam(Canvas c, float x, float y, float w, float h) {
        JSONObject ram = s.optJSONObject("ram");
        if (ram == null) return;
        titulo(c, T.t("Memoria", "Memory"), x, y, w);
        float pct = (float) ram.optDouble("porcentaje", 0);
        String sub = String.format(Locale.ROOT, "%.1f / %.1f GB",
                ram.optDouble("usada_gb", 0), ram.optDouble("total_gb", 0));
        float radio = Math.min(w, h - 20 * dp) / 2f * 0.85f;
        anillo(c, x + w / 2, y + 16 * dp + (h - 16 * dp) / 2, radio, pct, Math.round(pct) + "%", sub);
    }

    private void tarjetaGpu(Canvas c, float x, float y, float w, float h) {
        JSONObject g = primeraGpu();
        if (g == null) {
            titulo(c, "GPU", x, y, w);
            pTexto.setTextAlign(Paint.Align.CENTER);
            pTexto.setColor(TENUE);
            pTexto.setTextSize(13 * dp);
            c.drawText(encajar(T.t("No detectada", "Not detected"), w), x + w / 2, y + h / 2, pTexto);
            pTexto.setTextSize(13 * dp);
            c.drawText(encajar(T.t("(se lee con nvidia-smi)", "(read with nvidia-smi)"), w), x + w / 2, y + h / 2 + 18 * dp, pTexto);
            return;
        }
        titulo(c, "GPU · " + recortar(g.optString("nombre", ""), 22), x, y, w);
        float uso = (float) g.optDouble("uso", 0);
        double t = g.optDouble("temp", Double.NaN);
        String sub = Double.isNaN(t) ? null : Math.round(t) + " °C";
        float grafH = h * 0.22f;
        float radio = Math.min(w, h - grafH - 34 * dp) / 2f * 0.85f;
        anillo(c, x + w / 2, y + 16 * dp + (h - grafH - 30 * dp) / 2, radio, uso, Math.round(uso) + "%", sub);
        double vu = g.optDouble("vram_usada_mb", 0), vt = g.optDouble("vram_total_mb", 0);
        pTexto.setTextAlign(Paint.Align.LEFT);
        pTexto.setColor(TENUE);
        pTexto.setTextSize(11 * dp);
        c.drawText(encajar(String.format(Locale.ROOT, "VRAM %.1f / %.1f GB", vu / 1024, vt / 1024), w),
                x, y + h - grafH - 6 * dp, pTexto);
        linea(c, histGpu, x, y + h - grafH, w, grafH, 100, SECUNDARIO);
    }

    private void tarjetaTemps(Canvas c, float x, float y, float w, float h) {
        titulo(c, T.t("Temperaturas", "Temperatures"), x, y, w);
        JSONArray t = s.optJSONArray("temperaturas");
        if (t == null || t.length() == 0) {
            pTexto.setTextAlign(Paint.Align.CENTER);
            pTexto.setColor(TENUE);
            pTexto.setTextSize(13 * dp);
            c.drawText(encajar(T.t("Sin sensores", "No sensors"), w), x + w / 2, y + h / 2, pTexto);
            pTexto.setTextSize(13 * dp);
            c.drawText(encajar(T.t("Abre LibreHardwareMonitor", "Open LibreHardwareMonitor"), w), x + w / 2, y + h / 2 + 18 * dp, pTexto);
            pTexto.setTextSize(13 * dp);
            c.drawText(encajar(T.t("con servidor web activado", "with the web server on"), w), x + w / 2, y + h / 2 + 34 * dp, pTexto);
            return;
        }
        float fila = 20 * dp;
        int caben = (int) ((h - 22 * dp) / fila);
        float yy = y + 22 * dp;
        for (int i = 0; i < t.length() && i < caben; i++) {
            JSONObject o = t.optJSONObject(i);
            if (o == null) continue;
            float v = (float) o.optDouble("valor", 0);
            String nombre = recortar(o.optString("sensor", ""), 20);
            String grados = Math.round(v) + " °C";
            pTexto.setTextSize(12 * dp);
            float anchoV = pTexto.measureText(grados);
            pTexto.setTextAlign(Paint.Align.LEFT);
            pTexto.setColor(TEXTO);
            c.drawText(encajar(nombre, w - anchoV - 6 * dp), x, yy + 12 * dp, pTexto);
            pTexto.setTextSize(12 * dp);
            pTexto.setTextAlign(Paint.Align.RIGHT);
            pTexto.setColor(colorPorNivel(v, 70, 85));
            c.drawText(grados, x + w, yy + 12 * dp, pTexto);
            barra(c, x, yy + 15 * dp, w, 3 * dp, v, colorPorNivel(v, 70, 85));
            yy += fila;
        }
    }

    private void tarjetaRed(Canvas c, float x, float y, float w, float h) {
        JSONObject red = s.optJSONObject("red");
        if (red == null) return;
        titulo(c, T.t("Red", "Network"), x, y, w);
        pTexto.setTextAlign(Paint.Align.LEFT);
        pTexto.setTextSize(18 * dp);
        pTexto.setColor(ACENTO);
        c.drawText(encajar("↓ " + velocidad(red.optDouble("bajada_kbs", 0)), w), x, y + 40 * dp, pTexto);
        pTexto.setTextSize(18 * dp);
        pTexto.setColor(SECUNDARIO);
        c.drawText(encajar("↑ " + velocidad(red.optDouble("subida_kbs", 0)), w), x, y + 64 * dp, pTexto);
        float gy = y + 74 * dp;
        if (h - (gy - y) > 20 * dp) {
            float max = 1;
            for (int i = 0; i < N; i++) max = Math.max(max, Math.max(histBajada[i], histSubida[i]));
            linea(c, histBajada, x, gy, w, y + h - gy, max, ACENTO);
            linea(c, histSubida, x, gy, w, y + h - gy, max, SECUNDARIO);
        }
    }

    private void tarjetaDisco(Canvas c, float x, float y, float w, float h) {
        JSONObject d = s.optJSONObject("disco");
        if (d == null) return;
        titulo(c, T.t("Discos", "Disks"), x, y, w);
        pTexto.setTextAlign(Paint.Align.LEFT);
        pTexto.setTextSize(12 * dp);
        pTexto.setColor(NUMEROS);
        c.drawText(encajar("L " + velocidad(d.optDouble("lectura_kbs", 0)) + "   E " + velocidad(d.optDouble("escritura_kbs", 0)), w),
                x, y + 34 * dp, pTexto);
        JSONArray u = d.optJSONArray("unidades");
        if (u == null) return;
        float fila = 24 * dp;
        float yy = y + 44 * dp;
        for (int i = 0; i < u.length() && yy + fila <= y + h + 4 * dp; i++) {
            JSONObject o = u.optJSONObject(i);
            if (o == null) continue;
            float pct = (float) o.optDouble("porcentaje", 0);
            String nombre = recortar(o.optString("unidad", ""), 12);
            String tam = String.format(Locale.ROOT, "%.0f / %.0f GB", o.optDouble("usado_gb", 0), o.optDouble("total_gb", 0));
            // El tamano va a la derecha y el nombre ocupa lo que quede, sin pisarse.
            pTexto.setTextSize(12 * dp);
            float anchoNombre = Math.min(pTexto.measureText(nombre), w * 0.35f);
            pTexto.setTextAlign(Paint.Align.RIGHT);
            pTexto.setColor(TENUE);
            tam = encajar(tam, w - anchoNombre - 8 * dp);
            float anchoTam = pTexto.measureText(tam);
            c.drawText(tam, x + w, yy + 12 * dp, pTexto);
            pTexto.setTextSize(12 * dp);
            pTexto.setTextAlign(Paint.Align.LEFT);
            pTexto.setColor(TEXTO);
            c.drawText(encajar(nombre, w - anchoTam - 8 * dp), x, yy + 12 * dp, pTexto);
            barra(c, x, yy + 16 * dp, w, 4 * dp, pct, colorPorNivel(pct, 80, 92));
            yy += fila;
        }
    }

    private void tarjetaNucleos(Canvas c, float x, float y, float w, float h) {
        JSONObject cpu = s.optJSONObject("cpu");
        JSONArray n = cpu != null ? cpu.optJSONArray("nucleos") : null;
        titulo(c, T.t("Núcleos", "Cores"), x, y, w);
        if (n == null || n.length() == 0) return;
        int k = n.length();
        int cols = k > 8 ? 2 : 1;
        int filas = (k + cols - 1) / cols;
        float top = y + 22 * dp;
        float fila = (h - 22 * dp) / filas;
        float colW = (w - (cols - 1) * 12 * dp) / cols;
        float alto = Math.max(3 * dp, Math.min(10 * dp, fila * 0.35f));
        pTexto.setTextSize(Math.max(9 * dp, Math.min(12 * dp, fila * 0.45f)));
        for (int i = 0; i < k; i++) {
            float v = (float) n.optDouble(i, 0);
            float cx = x + (i / filas) * (colW + 12 * dp);
            float cy = top + (i % filas) * fila;
            pTexto.setTextAlign(Paint.Align.LEFT);
            pTexto.setColor(TENUE);
            c.drawText(String.valueOf(i), cx, cy + fila / 2 + 4 * dp, pTexto);
            pTexto.setTextAlign(Paint.Align.RIGHT);
            pTexto.setColor(NUMEROS);
            c.drawText(Math.round(v) + "%", cx + colW, cy + fila / 2 + 4 * dp, pTexto);
            barra(c, cx + 20 * dp, cy + fila / 2 - alto / 2, colW - 58 * dp, alto, v, colorPorNivel(v, 70, 90));
        }
    }

    private void filaDato(Canvas c, String nombre, String valor, float x, float y, float w) {
        pTexto.setTextSize(13 * dp);
        pTexto.setTextAlign(Paint.Align.RIGHT);
        pTexto.setColor(NUMEROS);
        valor = encajar(valor, w * 0.6f);
        float anchoV = pTexto.measureText(valor);
        c.drawText(valor, x + w, y, pTexto);
        pTexto.setTextSize(13 * dp);
        pTexto.setTextAlign(Paint.Align.LEFT);
        pTexto.setColor(TENUE);
        c.drawText(encajar(nombre, w - anchoV - 8 * dp), x, y, pTexto);
    }

    private static String num(JSONObject o, String k, String fmt) {
        double v = o.optDouble(k, Double.NaN);
        return Double.isNaN(v) ? "-" : String.format(Locale.ROOT, fmt, v);
    }

    private void tarjetaGpuDetalle(Canvas c, float x, float y, float w, float h) {
        JSONObject g = primeraGpu();
        titulo(c, g == null ? "GPU" : "GPU · " + recortar(g.optString("nombre", ""), 24), x, y, w);
        if (g == null) return;
        float fila = 22 * dp, yy = y + 38 * dp;
        String[][] datos = {
                {T.t("Uso", "Usage"), num(g, "uso", "%.0f %%")},
                {T.t("Temperatura", "Temperature"), num(g, "temp", "%.0f °C")},
                {T.t("Reloj núcleo", "Core clock"), num(g, "reloj_mhz", "%.0f MHz")},
                {T.t("Reloj memoria", "Memory clock"), num(g, "reloj_mem_mhz", "%.0f MHz")},
                {T.t("Consumo", "Power"), num(g, "consumo_w", "%.0f W") + " / " + num(g, "limite_w", "%.0f W")},
                {T.t("Ventilador", "Fan"), num(g, "ventilador", "%.0f %%")},
                {"VRAM", String.format(Locale.ROOT, "%.0f / %.0f MB",
                        g.optDouble("vram_usada_mb", 0), g.optDouble("vram_total_mb", 0))},
        };
        for (String[] d : datos) {
            if (yy > y + h) break;
            filaDato(c, d[0], d[1], x, yy, w);
            yy += fila;
        }
        if (yy < y + h - 30 * dp) linea(c, histGpu, x, yy, w, y + h - yy, 100, SECUNDARIO);
    }

    private void tarjetaVentiladores(Canvas c, float x, float y, float w, float h) {
        titulo(c, T.t("Ventiladores y consumo", "Fans and power"), x, y, w);
        float fila = 22 * dp, yy = y + 38 * dp;
        JSONArray f = s.optJSONArray("ventiladores");
        JSONArray p = s.optJSONArray("consumos");
        boolean nada = true;
        for (int i = 0; f != null && i < f.length() && yy <= y + h; i++) {
            JSONObject o = f.optJSONObject(i);
            if (o == null || o.optDouble("rpm", 0) <= 0) continue;
            filaDato(c, recortar(o.optString("sensor", ""), 18), num(o, "rpm", "%.0f RPM"), x, yy, w);
            yy += fila;
            nada = false;
        }
        for (int i = 0; p != null && i < p.length() && yy <= y + h; i++) {
            JSONObject o = p.optJSONObject(i);
            if (o == null) continue;
            filaDato(c, recortar(o.optString("dispositivo", "") + " " + o.optString("sensor", ""), 22),
                    num(o, "w", "%.1f W"), x, yy, w);
            yy += fila;
            nada = false;
        }
        if (nada) {
            pTexto.setTextAlign(Paint.Align.CENTER);
            pTexto.setColor(TENUE);
            pTexto.setTextSize(13 * dp);
            c.drawText(encajar(T.t("Necesita LibreHardwareMonitor", "Needs LibreHardwareMonitor"), w), x + w / 2, y + h / 2, pTexto);
            pTexto.setTextSize(13 * dp);
            c.drawText(encajar(T.t("con servidor web activado", "with the web server on"), w), x + w / 2, y + h / 2 + 18 * dp, pTexto);
        }
    }

    // --------------------------------------------------------------- formato --

    private int colorPorNivel(float v, float aviso, float alerta) {
        return v >= alerta ? ALERTA : v >= aviso ? AVISO : ACENTO;
    }

    private static String velocidad(double kbs) {
        if (kbs >= 1024) return String.format(Locale.ROOT, "%.1f MB/s", kbs / 1024);
        return String.format(Locale.ROOT, "%.0f KB/s", kbs);
    }

    private static String duracion(double seg) {
        long m = (long) (seg / 60), hh = m / 60, dd = hh / 24;
        if (dd > 0) return dd + "d " + (hh % 24) + "h";
        return hh + "h " + (m % 60) + "m";
    }

    private static String recortar(String t, int n) {
        return t.length() <= n ? t : t.substring(0, n - 1) + "…";
    }
}
