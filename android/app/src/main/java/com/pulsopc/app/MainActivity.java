package com.pulsopc.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.DialogInterface;
import android.content.SharedPreferences;
import android.net.wifi.WifiManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.View;
import android.view.WindowManager;
import android.widget.EditText;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.HttpURLConnection;
import java.net.InetSocketAddress;
import java.net.SocketTimeoutException;
import java.net.URL;

/**
 * Pantalla unica: busca el agente del PC en la Wi-Fi (o usa la IP guardada),
 * pide /stats cada segundo y lo dibuja en PanelView.
 * Mantener pulsado el panel abre el ajuste manual de IP.
 */
public class MainActivity extends Activity {

    static final int PUERTO_DESCUBRIMIENTO = 47800;
    static final int PUERTO_HTTP = 47801;
    static final long INTERVALO_MS = 1000;
    /** Por cable: el agente hace "adb reverse" y el PC aparece en el propio movil. */
    static final String HOST_USB = "127.0.0.1:" + PUERTO_HTTP;

    private PanelView panel;
    private SharedPreferences prefs;
    private final Handler ui = new Handler(Looper.getMainLooper());

    private volatile String host;          // "192.168.1.20:47801"
    private volatile boolean activo;
    private Thread hiloSondeo, hiloDescubrir;
    private WifiManager.MulticastLock multicastLock;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        // Pensado para un movil fijo dentro del PC: pantalla siempre encendida
        // y visible por encima de la pantalla de bloqueo (sin PIN).
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
                | WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED
                | WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON
                | WindowManager.LayoutParams.FLAG_DISMISS_KEYGUARD);
        panel = new PanelView(this);
        setContentView(panel);
        prefs = getSharedPreferences("pulsopc", MODE_PRIVATE);
        host = prefs.getString("host", null);

        panel.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) { panel.siguientePagina(); }
        });
        panel.setOyenteAccion(new PanelView.OyenteAccion() {
            @Override public void onAccion(final String accion) {
                final String h = actual != null ? actual : host;
                if (h == null) return;
                new Thread(new Runnable() { public void run() { enviarAccion(h, accion); } }).start();
            }
        });
        panel.setOnLongClickListener(new View.OnLongClickListener() {
            @Override public boolean onLongClick(View v) { pedirIp(); return true; }
        });
    }

    @Override
    protected void onResume() {
        super.onResume();
        ocultarBarras();
        activo = true;
        WifiManager wm = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
        if (wm != null) {
            multicastLock = wm.createMulticastLock("pulsopc");
            multicastLock.setReferenceCounted(false);
            multicastLock.acquire();
        }
        hiloDescubrir = new Thread(new Runnable() { public void run() { bucleDescubrir(); } }, "descubrir");
        hiloSondeo = new Thread(new Runnable() { public void run() { bucleSondeo(); } }, "sondeo");
        hiloDescubrir.start();
        hiloSondeo.start();
    }

    @Override
    protected void onPause() {
        super.onPause();
        activo = false;
        if (hiloSondeo != null) hiloSondeo.interrupt();
        if (hiloDescubrir != null) hiloDescubrir.interrupt();
        if (multicastLock != null && multicastLock.isHeld()) multicastLock.release();
    }

    private void ocultarBarras() {
        getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                        | View.SYSTEM_UI_FLAG_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                        | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN);
    }

    // ------------------------------------------------------- descubrimiento --

    /** Escucha los anuncios UDP del agente y guarda su IP. */
    private void bucleDescubrir() {
        DatagramSocket s = null;
        try {
            s = new DatagramSocket(null);
            s.setReuseAddress(true);
            s.setBroadcast(true);
            s.bind(new InetSocketAddress(PUERTO_DESCUBRIMIENTO));
            s.setSoTimeout(1000);
            byte[] buf = new byte[1024];
            while (activo) {
                DatagramPacket p = new DatagramPacket(buf, buf.length);
                try {
                    s.receive(p);
                } catch (SocketTimeoutException e) {
                    continue;
                }
                try {
                    JSONObject j = new JSONObject(new String(p.getData(), 0, p.getLength(), "UTF-8"));
                    if (!"pulsopc".equals(j.optString("app"))) continue;
                    String nuevo = p.getAddress().getHostAddress() + ":" + j.optInt("puerto", PUERTO_HTTP);
                    // Solo adoptamos el anuncio si no tenemos host o el actual no responde.
                    if (host == null || actual == null) {
                        host = nuevo;
                        prefs.edit().putString("host", nuevo).apply();
                    }
                } catch (Exception ignorado) { }
            }
        } catch (Exception e) {
            // Sin descubrimiento automatico: queda la IP manual.
        } finally {
            if (s != null) s.close();
        }
    }

    // --------------------------------------------------------------- sondeo --

    private void bucleSondeo() {
        int ciclo = 0;
        while (activo) {
            long t0 = System.currentTimeMillis();
            ciclo++;
            JSONObject j = null;
            String via = null;

            // 1) USB primero: es lo mas estable. Si ya estamos por USB, se usa siempre;
            //    si estamos por Wi-Fi, se comprueba cada 5 s por si se ha enchufado el cable.
            boolean probarUsb = HOST_USB.equals(actual) || actual == null || ciclo % 5 == 0;
            if (probarUsb) {
                j = intentar(HOST_USB, HOST_USB.equals(actual) ? 1500 : 400);
                if (j != null) via = HOST_USB;
            }
            // 2) Wi-Fi: la IP descubierta o escrita a mano.
            final String h = host;
            if (j == null && h != null && !h.equals(HOST_USB)) {
                j = intentar(h, 1500);
                if (j != null) via = h;
            }

            actual = via;
            if (j != null) actualizarPortada(via, j.optJSONObject("medios"));
            if (j != null && j.has("cpu")) {
                panel.setConexion(HOST_USB.equals(via) ? "USB" : "Wi-Fi " + via.split(":")[0]);
                publicar(j, null);
            } else if (j != null) {
                publicar(null, "Conectado, esperando datos...");
            } else if (h == null) {
                publicar(null, "Buscando el PC (Wi-Fi o cable USB)...\nMantén pulsado para escribir la IP.");
            } else {
                publicar(null, "Sin respuesta de " + h + " ni por USB\nMantén pulsado para cambiar la IP.");
            }

            long espera = INTERVALO_MS - (System.currentTimeMillis() - t0);
            if (espera > 0) {
                try { Thread.sleep(espera); } catch (InterruptedException e) { return; }
            }
        }
    }

    private volatile String actual;

    private static JSONObject intentar(String h, int timeoutMs) {
        try {
            return new JSONObject(pedir("http://" + h + "/stats", timeoutMs));
        } catch (Exception e) {
            return null;
        }
    }

    private String portadaId = "";

    /** Descarga la portada del disco solo cuando cambia la cancion. */
    private void actualizarPortada(String h, JSONObject medios) {
        String id = medios != null ? medios.optString("portada_id", "") : "";
        if (id.equals(portadaId)) return;
        portadaId = id;
        android.graphics.Bitmap bmp = null;
        if (!id.isEmpty()) {
            try {
                HttpURLConnection c = (HttpURLConnection) new URL("http://" + h + "/portada").openConnection();
                c.setConnectTimeout(1500);
                c.setReadTimeout(3000);
                InputStream in = c.getInputStream();
                ByteArrayOutputStream out = new ByteArrayOutputStream();
                byte[] b = new byte[16384];
                int n;
                while ((n = in.read(b)) > 0) out.write(b, 0, n);
                c.disconnect();
                byte[] datos = out.toByteArray();
                android.graphics.BitmapFactory.Options o = new android.graphics.BitmapFactory.Options();
                o.inJustDecodeBounds = true;
                android.graphics.BitmapFactory.decodeByteArray(datos, 0, datos.length, o);
                int escala = 1;
                while (o.outWidth / (escala * 2) >= 256 && o.outHeight / (escala * 2) >= 256) escala *= 2;
                o = new android.graphics.BitmapFactory.Options();
                o.inSampleSize = escala;
                bmp = android.graphics.BitmapFactory.decodeByteArray(datos, 0, datos.length, o);
            } catch (Exception e) {
                portadaId = "";   // reintentar en la siguiente vuelta
            }
        }
        final android.graphics.Bitmap fin = bmp;
        ui.post(new Runnable() { public void run() { panel.setPortada(fin); } });
    }

    /** Boton multimedia del movil: el agente pulsa la tecla multimedia en el PC. */
    private static void enviarAccion(String h, String accion) {
        try {
            HttpURLConnection c = (HttpURLConnection) new URL("http://" + h + "/accion").openConnection();
            c.setConnectTimeout(1500);
            c.setReadTimeout(1500);
            c.setDoOutput(true);
            c.setRequestMethod("POST");
            c.setRequestProperty("Content-Type", "application/json");
            byte[] cuerpo = new JSONObject().put("accion", accion).toString().getBytes("UTF-8");
            c.getOutputStream().write(cuerpo);
            c.getInputStream().close();
            c.disconnect();
        } catch (Exception ignorado) { }
    }

    private static String pedir(String url, int timeoutMs) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(timeoutMs);
        c.setReadTimeout(1500);
        c.setUseCaches(false);
        try {
            InputStream in = c.getInputStream();
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] b = new byte[8192];
            int n;
            while ((n = in.read(b)) > 0) out.write(b, 0, n);
            return out.toString("UTF-8");
        } finally {
            c.disconnect();
        }
    }

    private void publicar(final JSONObject datos, final String aviso) {
        ui.post(new Runnable() {
            @Override public void run() {
                if (datos != null) panel.actualizar(datos);
                else panel.aviso(aviso);
                ajustarBrillo();
            }
        });
    }

    private boolean atenuado;

    /** Con el PC apagado se baja el brillo al minimo; al volver los datos, brillo normal. */
    private void ajustarBrillo() {
        boolean apagar = panel.pcApagado();
        if (apagar == atenuado) return;
        atenuado = apagar;
        WindowManager.LayoutParams lp = getWindow().getAttributes();
        lp.screenBrightness = apagar ? 0.01f : WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE;
        getWindow().setAttributes(lp);
    }

    // ------------------------------------------------------------ IP manual --

    private void pedirIp() {
        final EditText campo = new EditText(this);
        campo.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        campo.setHint("192.168.1.20:" + PUERTO_HTTP);
        if (host != null) campo.setText(host);
        new AlertDialog.Builder(this)
                .setTitle("IP del PC")
                .setMessage("La ves en la ventana del agente al arrancarlo. Por cable USB no hace falta.")
                .setView(campo)
                .setPositiveButton("Guardar", new DialogInterface.OnClickListener() {
                    @Override public void onClick(DialogInterface d, int w) {
                        String v = campo.getText().toString().trim();
                        if (v.startsWith("http://")) v = v.substring(7);
                        if (v.endsWith("/")) v = v.substring(0, v.length() - 1);
                        if (v.isEmpty()) return;
                        if (!v.contains(":")) v = v + ":" + PUERTO_HTTP;
                        host = v;
                        prefs.edit().putString("host", v).apply();
                        Toast.makeText(MainActivity.this, "Conectando a " + v, Toast.LENGTH_SHORT).show();
                    }
                })
                .setNegativeButton("Cancelar", null)
                .setOnDismissListener(new DialogInterface.OnDismissListener() {
                    @Override public void onDismiss(DialogInterface d) { ocultarBarras(); }
                })
                .show();
    }
}
