package com.pulsopc.app;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/**
 * Abre PulsoPC solo, sin tocar el movil:
 *  - al terminar de encender el movil (BOOT_COMPLETED)
 *  - al recibir corriente por el cable (POWER_CONNECTED), p. ej. al encender el PC.
 * En Android 6 ambos avisos llegan a un receptor declarado en el manifiesto.
 */
public class AutoArranque extends BroadcastReceiver {
    @Override
    public void onReceive(Context c, Intent intent) {
        if (!c.getSharedPreferences("pulsopc", Context.MODE_PRIVATE).getBoolean("auto_arranque", true)) return;
        Intent abrir = new Intent(c, MainActivity.class);
        abrir.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT);
        c.startActivity(abrir);
    }
}
