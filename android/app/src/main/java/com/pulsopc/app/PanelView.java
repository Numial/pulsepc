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
 * en moviles antiguos. Seis tarjetas: CPU, RAM, GPU, temperaturas, red y disco.
 * En horizontal 3x2, en vertical 2x3.
 */
public class PanelView extends View {

    // Paleta propia: fondo grafito, acento verde agua, aviso ambar, alerta coral.
    static final int FONDO = 0xFF0E1116, TARJETA = 0xFF171B22, PISTA = 0xFF2A303A;
    static final int TEXTO = 0xFFE6E6E6, TENUE = 0xFF8C96A4;
    static final int ACENTO = 0xFF3EC7A0, SECUNDARIO = 0xFF5B9BF0, AVISO = 0xFFF2B33D, ALERTA = 0xFFF0645B;

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

    public PanelView(Context c) {
        super(c);
        dp = c.getResources().getDisplayMetrics().density;
        pTrazo.setStyle(Paint.Style.STROKE);
        pTrazo.setStrokeCap(Paint.Cap.ROUND);
        pTexto.setTypeface(Typeface.create("sans-serif-condensed", Typeface.NORMAL));
        setLongClickable(true);
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
        float m = 8 * dp;
        float alturaCab = 28 * dp;

        dibujarCabecera(c, m, m, w - 2 * m, alturaCab);

        if (s == null) {
            pTexto.setColor(TENUE);
            pTexto.setTextSize(16 * dp);
            pTexto.setTextAlign(Paint.Align.CENTER);
            String[] lineas = (aviso == null ? "" : aviso).split("\n");
            for (int i = 0; i < lineas.length; i++)
                c.drawText(lineas[i], w / 2f, h / 2f + i * 22 * dp, pTexto);
            return;
        }

        boolean horizontal = w > h;
        int cols = horizontal ? 3 : 2, filas = horizontal ? 2 : 3;
        float top = m + alturaCab + m;
        float tw = (w - m * (cols + 1)) / cols;
        float th = (h - top - m * filas) / filas;

        for (int i = 0; i < 6; i++) {
            float x = m + (i % cols) * (tw + m);
            float y = top + (i / cols) * (th + m);
            r.set(x, y, x + tw, y + th);
            pRelleno.setColor(TARJETA);
            c.drawRoundRect(r, 10 * dp, 10 * dp, pRelleno);
            float p = 10 * dp;
            switch (i) {
                case 0: tarjetaCpu(c, x + p, y + p, tw - 2 * p, th - 2 * p); break;
                case 1: tarjetaRam(c, x + p, y + p, tw - 2 * p, th - 2 * p); break;
                case 2: tarjetaGpu(c, x + p, y + p, tw - 2 * p, th - 2 * p); break;
                case 3: tarjetaTemps(c, x + p, y + p, tw - 2 * p, th - 2 * p); break;
                case 4: tarjetaRed(c, x + p, y + p, tw - 2 * p, th - 2 * p); break;
                case 5: tarjetaDisco(c, x + p, y + p, tw - 2 * p, th - 2 * p); break;
            }
        }
    }

    private void dibujarCabecera(Canvas c, float x, float y, float w, float h) {
        boolean ok = conectado();
        pRelleno.setColor(ok ? ACENTO : ALERTA);
        c.drawCircle(x + 6 * dp, y + h / 2, 5 * dp, pRelleno);
        pTexto.setTextAlign(Paint.Align.LEFT);
        pTexto.setColor(TEXTO);
        pTexto.setTextSize(16 * dp);
        String equipo = s != null ? s.optString("equipo", "PC") : "PulsoPC";
        c.drawText(equipo, x + 18 * dp, y + h / 2 + 6 * dp, pTexto);
        if (s != null) {
            pTexto.setTextAlign(Paint.Align.RIGHT);
            pTexto.setColor(TENUE);
            pTexto.setTextSize(12 * dp);
            String extra = s.optString("so", "") + "  ·  encendido " + duracion(s.optDouble("uptime_s", 0));
            if (!ok) extra = "sin conexión  ·  " + extra;
            c.drawText(extra, x + w, y + h / 2 + 5 * dp, pTexto);
        }
    }

    private void titulo(Canvas c, String t, float x, float y) {
        pTexto.setTextAlign(Paint.Align.LEFT);
        pTexto.setColor(TENUE);
        pTexto.setTextSize(12 * dp);
        c.drawText(t.toUpperCase(Locale.ROOT), x, y + 12 * dp, pTexto);
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
        c.drawText(centro, cx, cy + radio * 0.17f, pTexto);
        if (sub != null) {
            pTexto.setColor(TENUE);
            pTexto.setTextSize(Math.max(10 * dp, radio * 0.2f));
            c.drawText(sub, cx, cy + radio * 0.6f, pTexto);
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
        titulo(c, "CPU", x, y);
        float uso = (float) cpu.optDouble("uso", 0);
        double f = cpu.optDouble("freq_mhz", Double.NaN);
        String sub = Double.isNaN(f) ? null : String.format(Locale.ROOT, "%.2f GHz", f / 1000);
        float grafH = h * 0.22f;
        float radio = Math.min(w, h - grafH - 20 * dp) / 2f * 0.85f;
        anillo(c, x + w / 2, y + 16 * dp + (h - grafH - 16 * dp) / 2, radio, uso,
                Math.round(uso) + "%", sub);
        linea(c, histCpu, x, y + h - grafH, w, grafH, 100, ACENTO);
    }

    private void tarjetaRam(Canvas c, float x, float y, float w, float h) {
        JSONObject ram = s.optJSONObject("ram");
        if (ram == null) return;
        titulo(c, "Memoria", x, y);
        float pct = (float) ram.optDouble("porcentaje", 0);
        String sub = String.format(Locale.ROOT, "%.1f / %.1f GB",
                ram.optDouble("usada_gb", 0), ram.optDouble("total_gb", 0));
        float radio = Math.min(w, h - 20 * dp) / 2f * 0.85f;
        anillo(c, x + w / 2, y + 16 * dp + (h - 16 * dp) / 2, radio, pct, Math.round(pct) + "%", sub);
    }

    private void tarjetaGpu(Canvas c, float x, float y, float w, float h) {
        JSONObject g = primeraGpu();
        if (g == null) {
            titulo(c, "GPU", x, y);
            pTexto.setTextAlign(Paint.Align.CENTER);
            pTexto.setColor(TENUE);
            pTexto.setTextSize(13 * dp);
            c.drawText("No detectada", x + w / 2, y + h / 2, pTexto);
            c.drawText("(se lee con nvidia-smi)", x + w / 2, y + h / 2 + 18 * dp, pTexto);
            return;
        }
        titulo(c, "GPU · " + recortar(g.optString("nombre", ""), 22), x, y);
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
        c.drawText(String.format(Locale.ROOT, "VRAM %.1f / %.1f GB", vu / 1024, vt / 1024),
                x, y + h - grafH - 6 * dp, pTexto);
        linea(c, histGpu, x, y + h - grafH, w, grafH, 100, SECUNDARIO);
    }

    private void tarjetaTemps(Canvas c, float x, float y, float w, float h) {
        titulo(c, "Temperaturas", x, y);
        JSONArray t = s.optJSONArray("temperaturas");
        if (t == null || t.length() == 0) {
            pTexto.setTextAlign(Paint.Align.CENTER);
            pTexto.setColor(TENUE);
            pTexto.setTextSize(13 * dp);
            c.drawText("Sin sensores", x + w / 2, y + h / 2, pTexto);
            c.drawText("Abre LibreHardwareMonitor", x + w / 2, y + h / 2 + 18 * dp, pTexto);
            c.drawText("con servidor web activado", x + w / 2, y + h / 2 + 34 * dp, pTexto);
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
            pTexto.setTextAlign(Paint.Align.LEFT);
            pTexto.setColor(TEXTO);
            pTexto.setTextSize(12 * dp);
            c.drawText(nombre, x, yy + 12 * dp, pTexto);
            pTexto.setTextAlign(Paint.Align.RIGHT);
            pTexto.setColor(colorPorNivel(v, 70, 85));
            c.drawText(Math.round(v) + " °C", x + w, yy + 12 * dp, pTexto);
            barra(c, x, yy + 15 * dp, w, 3 * dp, v, colorPorNivel(v, 70, 85));
            yy += fila;
        }
    }

    private void tarjetaRed(Canvas c, float x, float y, float w, float h) {
        JSONObject red = s.optJSONObject("red");
        if (red == null) return;
        titulo(c, "Red", x, y);
        pTexto.setTextAlign(Paint.Align.LEFT);
        pTexto.setTextSize(18 * dp);
        pTexto.setColor(ACENTO);
        c.drawText("↓ " + velocidad(red.optDouble("bajada_kbs", 0)), x, y + 40 * dp, pTexto);
        pTexto.setColor(SECUNDARIO);
        c.drawText("↑ " + velocidad(red.optDouble("subida_kbs", 0)), x, y + 64 * dp, pTexto);
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
        titulo(c, "Discos", x, y);
        pTexto.setTextAlign(Paint.Align.LEFT);
        pTexto.setTextSize(12 * dp);
        pTexto.setColor(TEXTO);
        c.drawText("L " + velocidad(d.optDouble("lectura_kbs", 0)) + "   E " + velocidad(d.optDouble("escritura_kbs", 0)),
                x, y + 34 * dp, pTexto);
        JSONArray u = d.optJSONArray("unidades");
        if (u == null) return;
        float fila = 24 * dp;
        float yy = y + 44 * dp;
        for (int i = 0; i < u.length() && yy + fila <= y + h + 4 * dp; i++) {
            JSONObject o = u.optJSONObject(i);
            if (o == null) continue;
            float pct = (float) o.optDouble("porcentaje", 0);
            pTexto.setTextAlign(Paint.Align.LEFT);
            pTexto.setColor(TEXTO);
            c.drawText(recortar(o.optString("unidad", ""), 12), x, yy + 12 * dp, pTexto);
            pTexto.setTextAlign(Paint.Align.RIGHT);
            pTexto.setColor(TENUE);
            c.drawText(String.format(Locale.ROOT, "%.0f / %.0f GB", o.optDouble("usado_gb", 0), o.optDouble("total_gb", 0)),
                    x + w, yy + 12 * dp, pTexto);
            barra(c, x, yy + 16 * dp, w, 4 * dp, pct, colorPorNivel(pct, 80, 92));
            yy += fila;
        }
    }

    // --------------------------------------------------------------- formato --

    private static int colorPorNivel(float v, float aviso, float alerta) {
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
