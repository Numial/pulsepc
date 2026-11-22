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
    private final RectF r = new RectF();
    private final Path ruta = new Path();

    private JSONObject s;
    private String aviso = "Iniciando...";
    private long ultimaRespuesta;

    private final float[] histCpu = new float[N], histGpu = new float[N];
    private final float[] histBajada = new float[N], histSubida = new float[N];
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
            TENUE = (TEXTO & 0x00FFFFFF) | 0x99000000;
            PISTA = (TEXTO & 0x00FFFFFF) | 0x22000000;
        }
        cabecera = d.optBoolean("cabecera", true);
        JSONArray p = d.optJSONArray("paginas");
        if (p != null && p.length() > 0) paginas = p;
        if (paginas != null && pagina >= paginas.length()) pagina = 0;
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
            c.drawText("PulsoPC · esperando al PC", px, py, pTexto);
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
            c.drawRoundRect(r, 10 * dp, 10 * dp, pRelleno);
            float pd = 10 * dp;
            tituloActual = wd.optString("titulo", "");
            dibujarWidget(c, wd.optString("tipo", ""), x + pd, y + pd, tw - 2 * pd, th - 2 * pd);
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
        }
    }

    private void tarjetaReloj(Canvas c, float x, float y, float w, float h) {
        titulo(c, "Reloj", x, y, w);
        java.util.Date ahora = new java.util.Date();
        String hora = new java.text.SimpleDateFormat("HH:mm", Locale.getDefault()).format(ahora);
        String fecha = new java.text.SimpleDateFormat("EEEE d MMMM", new Locale("es", "ES")).format(ahora);
        pTexto.setTextAlign(Paint.Align.CENTER);
        pTexto.setColor(TEXTO);
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
        String equipo = s != null ? s.optString("equipo", "PC") : "PulsoPC";
        equipo = encajar(equipo, w * 0.5f);
        float finEquipo = x + 18 * dp + pTexto.measureText(equipo);
        c.drawText(equipo, x + 18 * dp, y + h / 2 + 6 * dp, pTexto);
        if (s != null) {
            pTexto.setTextAlign(Paint.Align.RIGHT);
            pTexto.setColor(TENUE);
            pTexto.setTextSize(12 * dp);
            String extra = (conexion.isEmpty() ? "" : conexion + "  ·  ") + s.optString("so", "")
                    + "  ·  encendido " + duracion(s.optDouble("uptime_s", 0));
            if (!ok) extra = "sin conexión  ·  " + extra;
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
        pTexto.setColor(TEXTO);
        pTexto.setTextSize(radio * 0.5f);
        c.drawText(encajar(centro, radio * 1.6f), cx, cy + radio * 0.17f, pTexto);
        if (sub != null) {
            pTexto.setColor(TENUE);
            pTexto.setTextSize(Math.max(10 * dp, radio * 0.2f));
            c.drawText(encajar(sub, radio * 1.6f), cx, cy + radio * 0.6f, pTexto);
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
        titulo(c, "Memoria", x, y, w);
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
            c.drawText(encajar("No detectada", w), x + w / 2, y + h / 2, pTexto);
            pTexto.setTextSize(13 * dp);
            c.drawText(encajar("(se lee con nvidia-smi)", w), x + w / 2, y + h / 2 + 18 * dp, pTexto);
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
        titulo(c, "Temperaturas", x, y, w);
        JSONArray t = s.optJSONArray("temperaturas");
        if (t == null || t.length() == 0) {
            pTexto.setTextAlign(Paint.Align.CENTER);
            pTexto.setColor(TENUE);
            pTexto.setTextSize(13 * dp);
            c.drawText(encajar("Sin sensores", w), x + w / 2, y + h / 2, pTexto);
            pTexto.setTextSize(13 * dp);
            c.drawText(encajar("Abre LibreHardwareMonitor", w), x + w / 2, y + h / 2 + 18 * dp, pTexto);
            pTexto.setTextSize(13 * dp);
            c.drawText(encajar("con servidor web activado", w), x + w / 2, y + h / 2 + 34 * dp, pTexto);
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
        titulo(c, "Red", x, y, w);
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
        titulo(c, "Discos", x, y, w);
        pTexto.setTextAlign(Paint.Align.LEFT);
        pTexto.setTextSize(12 * dp);
        pTexto.setColor(TEXTO);
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
        titulo(c, "Núcleos", x, y, w);
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
            pTexto.setColor(TEXTO);
            c.drawText(Math.round(v) + "%", cx + colW, cy + fila / 2 + 4 * dp, pTexto);
            barra(c, cx + 20 * dp, cy + fila / 2 - alto / 2, colW - 58 * dp, alto, v, colorPorNivel(v, 70, 90));
        }
    }

    private void filaDato(Canvas c, String nombre, String valor, float x, float y, float w) {
        pTexto.setTextSize(13 * dp);
        pTexto.setTextAlign(Paint.Align.RIGHT);
        pTexto.setColor(TEXTO);
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
                {"Uso", num(g, "uso", "%.0f %%")},
                {"Temperatura", num(g, "temp", "%.0f °C")},
                {"Reloj núcleo", num(g, "reloj_mhz", "%.0f MHz")},
                {"Reloj memoria", num(g, "reloj_mem_mhz", "%.0f MHz")},
                {"Consumo", num(g, "consumo_w", "%.0f W") + " / " + num(g, "limite_w", "%.0f W")},
                {"Ventilador", num(g, "ventilador", "%.0f %%")},
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
        titulo(c, "Ventiladores y consumo", x, y, w);
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
            c.drawText(encajar("Necesita LibreHardwareMonitor", w), x + w / 2, y + h / 2, pTexto);
            pTexto.setTextSize(13 * dp);
            c.drawText(encajar("con servidor web activado", w), x + w / 2, y + h / 2 + 18 * dp, pTexto);
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
