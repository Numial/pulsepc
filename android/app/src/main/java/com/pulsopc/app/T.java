package com.pulsopc.app;

/** Idioma de la app: se elige en el PC (editor o icono de la bandeja) y llega con /stats. */
final class T {
    static volatile boolean en = java.util.Locale.getDefault().getLanguage().equals("en");

    private T() { }

    /** Devuelve el texto en el idioma elegido. */
    static String t(String es, String ingles) { return en ? ingles : es; }

    static java.util.Locale locale() { return en ? java.util.Locale.ENGLISH : new java.util.Locale("es", "ES"); }
}
