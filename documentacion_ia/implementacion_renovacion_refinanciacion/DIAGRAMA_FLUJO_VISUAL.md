# Diagrama de Flujo - Casos de Validación

## Árbol de Decisión Visual

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                  procesar_llamada_renovacionYrefinanciamiento               │
│                                                                             │
│  1. Extrae variables (nombres, correos, teléfonos)                         │
│  2. Determina destinatario (preferencia: cliente > guardado)               │
│  3. Evalúa TODOS los casos que cumplan condiciones                        │
│  4. Ejecuta acciones correspondientes                                      │
│  5. Retorna resumen de acciones y errores                                  │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                ┌───────────────────┼───────────────────┐
                │                   │                   │
                ▼                   ▼                   ▼
        ┌──────────────┐    ┌──────────────┐   ┌──────────────┐
        │ CASO 1       │    │ CASO 2       │   │ CASOS 3-6    │
        │ CASO 7       │    │              │   │              │
        │              │    │              │   │              │
        │ CORREO       │    │ CORREO       │   │ WEBHOOK      │
        └──────────────┘    └──────────────┘   └──────────────┘


══════════════════════════════════════════════════════════════════════════════

CASO 1: RENOVACIÓN + CORREO + WEBHOOK
──────────────────────────────────────────────────────────────────────────────

¿renovacion == "Si"?
        │
        ├─ NO ─────────────────────┐
        │                           │
        │                           ▼
        │                  ¿renovacion == "No"?
        │                          │
        │                          ├─ NO ─────────────────────┐
        │                          │                           │
        │                          │                           ▼
        │                          │                  Ir a CASO 3-6
        │                          │
        │                          └─ SÍ ─────────────────────┐
        │                                                      │
        │                                         ¿acpt_info_email OR
        │                                          aceptoinfocorreo?
        │                                                 │
        │                                    ┌────────────┴────────────┐
        │                                    │                         │
        │                                    NO                       SÍ
        │                                    │                         │
        │                                    │                         ▼
        │                                    │                 CASO 2: CORREO
        │                                    │                 (sin webhook)
        │                                    │
        └─ SÍ ───────────────────────────────┤
                                            ▼
                           ¿acpt_info_email OR aceptoinfocorreo?
                                            │
                              ┌─────────────┴─────────────┐
                              │                           │
                              NO                         SÍ
                              │                           │
                              │                           ▼
                              │                   CASO 1: CORREO + WEBHOOK
                              │                   ✅ Enviar correo
                              │                   ✅ Llamar webhook (tipo: renovacion)
                              │
                              ▼
                      Ir a CASO 3-6


══════════════════════════════════════════════════════════════════════════════

CASOS 3-6: REFINANCIAMIENTO
──────────────────────────────────────────────────────────────────────────────

                        ¿refinanciar_bool == True?
                                  │
                      ┌───────────┴───────────┐
                      │                       │
                      NO                     SÍ
                      │                       │
                    Saltar                    ▼
                                  ¿refinanciar == "Si"?
                                            │
                                ┌───────────┴───────────┐
                                │                       │
                                NO                     SÍ
                                │                       │
                              Saltar        CASO 3: WEBHOOK
                                            (tipo: refinanciamiento)
                                                      │
                                                      ▼
                                        ¿agendo_asst_assr == "Si"?
                                                  │
                                      ┌───────────┴───────────┐
                                      │                       │
                                      NO          CASO 4: WEBHOOK
                                      │        (tipo: refinanciamiento_con_asesoria)
                                      │                       │
                                      │                       ▼
                                      │              ¿fecha_asst_assor válida?
                                      │                      │
                                      │          ┌───────────┴───────────┐
                                      │          │                       │
                                      │          NO       CASO 5: WEBHOOK
                                      │          │    (con fecha incluida)
                                      │          │
                                      │          ▼
                                      │    ¿asst_assr_bool == True?
                                      │              │
                                      │   ┌──────────┴──────────┐
                                      │   │                     │
                                      │   NO                   SÍ
                                      │   │        CASO 6: WEBHOOK
                                      │   │ (tipo: asesoria_confirmada)
                                      │   │
                                      ▼   ▼
                                     Fin


══════════════════════════════════════════════════════════════════════════════

CASO 7: CORREO DE SEGUIMIENTO (CATCHALL)
──────────────────────────────────────────────────────────────────────────────

¿aceptoinfocorreo == "Si"
  AND refinanciar == "No"
  AND refinanciar_bool == False
  AND renovacion == "No"?
                │
        ┌───────┴───────┐
        │               │
        NO             SÍ
        │               │
      Fin       CASO 7: CORREO
              (Correo de seguimiento)


══════════════════════════════════════════════════════════════════════════════

MATRIZ DE COMBINACIONES
──────────────────────────────────────────────────────────────────────────────

Escenario                               │ Caso(s)   │ Acción(es)
────────────────────────────────────────┼───────────┼──────────────────────────
renovacion=Si + aceptación              │ CASO 1    │ Correo + Webhook
renovacion=No + aceptación              │ CASO 2    │ Solo Correo
refinanciar=Si + bool=True              │ CASO 3    │ Webhook
refinanciar=Si + bool=True + agendo=Si  │ CASO 4    │ Webhook (con asesoría)
CASO 4 + fecha válida                   │ CASO 5    │ Webhook (con fecha)
refinanciar=Si + bool=True + asr_bool   │ CASO 6    │ Webhook (confirmado)
Sin renovación/refinanciamiento + info  │ CASO 7    │ Solo Correo
Ninguna condición                       │ Ninguno   │ Warning (sin acción)


══════════════════════════════════════════════════════════════════════════════

EJEMPLO: Payload Completo (CASO 1 + CASO 7)
──────────────────────────────────────────────────────────────────────────────

Input:
{
  "renovacion": "Si",                    ◄─── CASO 1 se ejecuta
  "acpt_info_email": True,               ◄─── Condición cumplida
  "aceptoinfocorreo": "Si",              ◄─── Condición cumplida
  "refinanciar_bool": False,             ◄─── CASO 3-6 NO se ejecutan
  "refinanciar": "No",                   ◄─── CASO 3-6 NO se ejecutan
  ...
}

Ejecución:
1. ✅ CASO 1 detectado
   - Envía correo de renovación
   - Llama webhook tipo "renovacion"

2. ❌ CASO 2 NO detectado (renovacion != "No")

3. ❌ CASOS 3-6 NO detectados (refinanciar_bool != True)

4. ❌ CASO 7 NO detectado (renovacion != "No")

Output:
{
  "status": "success",
  "acciones_ejecutadas": [
    "correo_renovacion",
    "webhook_renovacion"
  ],
  "errores": null
}


══════════════════════════════════════════════════════════════════════════════

FLUJO DE ERRORES
──────────────────────────────────────────────────────────────────────────────

Envío de Correo Falla
        │
        ├─ Error: "No se envió correo"
        │  - Registra en logs
        │  - Llama error_notify()
        │  - Continúa evaluando otros casos
        │  - Documento error en lista

Petición Webhook Falla
        │
        ├─ ConnectError: "Error de conexión"
        ├─ TimeoutError: "Timeout en petición"
        ├─ HTTPError: "Webhook retornó status X"
        │
        ├─ Para cada error:
        │  - Registra en logs
        │  - Llama error_notify()
        │  - Continúa evaluando otros casos
        │  - Documenta error en lista
        │
        ▼
Respuesta con status="partial" + errores


══════════════════════════════════════════════════════════════════════════════

DETERMINACIÓN DE DESTINATARIO
──────────────────────────────────────────────────────────────────────────────

¿desicion_correo == True?
        │
        ├─ SÍ ──────────────► Usa CORREO guardado
        │
        └─ NO/None ◄─────┐
                         │
            ¿correo_cliente vacío?
                    │
         ┌──────────┴──────────┐
         │                     │
         SÍ                    NO
         │                     │
       Usa                   Usa
       CORREO           correo_cliente
       guardado


══════════════════════════════════════════════════════════════════════════════

ESTRUCTURA DE RESPUESTA
──────────────────────────────────────────────────────────────────────────────

Status = "success"
│
├─ "status": "success"
├─ "cliente": "darwin andres pacheco"
├─ "correo": "darwinandres901@gmail.com"
├─ "acciones_ejecutadas": ["correo_renovacion", "webhook_renovacion"]
└─ "errores": null


Status = "partial"
│
├─ "status": "partial"
├─ "cliente": "darwin andres pacheco"
├─ "correo": "darwinandres901@gmail.com"
├─ "acciones_ejecutadas": ["correo_renovacion"]
└─ "errores": ["Error: Webhook rechazado con status 500"]


Status = "error"
│
├─ "status": "error"
├─ "cliente": "darwin andres pacheco"
├─ "message": "Error en el procesamiento: [Excepción]"
└─ "acciones": []


Status = "warning"
│
├─ "status": "warning"
├─ "cliente": "darwin andres pacheco"
├─ "message": "No se ejecutó ninguna acción"
├─ "acciones": []
└─ "errores": []


═══════════════════════════════════════════════════════════════════════════════
```
