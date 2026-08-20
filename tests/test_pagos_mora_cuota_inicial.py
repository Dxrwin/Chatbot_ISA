import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import logica


def cuota(numero=1, estado=0, payment=1044248):
    return {
        "id": f"cuota-{numero}",
        "number": numero,
        "status": estado,
        "date": "2026-08-31T05:00:00Z",
        "payment": payment,
        "capital": payment - 100000,
        "interest": 80000,
        "costs": 16000,
        "taxes": 4000,
    }


def credito_con_cuota_inicial(approved_marker=None, incluir_approved=True, amount=736759):
    flow_payment = {
        "key": "cuota_inicial",
        "type": "initial_fee",
        "description": "cobro de cuota inicial",
        "amount": amount,
        "nextStatus": 5,
    }
    if incluir_approved:
        flow_payment["approved"] = approved_marker
    return {
        "ID": "credito-1",
        "status": 7,
        "initialFee": amount,
        "flowPayments": [flow_payment],
        "installments": [cuota(1, 0), cuota(2, 0)],
        "summary": {"debtDays": 0},
    }


class CuotaInicialPagosMoraTests(unittest.TestCase):
    def construir(self, credito):
        return logica.construir_data_pagos_mora_oficial(
            credito,
            id_credito_solicitado="credito-1",
            consulta_por_cedula=False,
        )

    def test_approved_ausente_deja_cuota_inicial_pendiente_y_prioritaria(self):
        resultado = self.construir(
            credito_con_cuota_inicial(incluir_approved=False)
        )
        data = resultado["data"]

        self.assertEqual(data["cuota_inicial"]["estado"], "pendiente")
        self.assertFalse(data["cuota_inicial"]["approved"])
        self.assertTrue(data["cuota_inicial"]["orden_habilitada"])
        self.assertEqual(data["resumen"]["inactivas_estado_0"], 2)
        self.assertIsNone(data["pago_pendiente"])
        self.assertEqual(data["pago_prioritario"]["tipo"], "cuota_inicial")
        self.assertEqual(data["pago_prioritario"]["id_cuota_orden"], "zero")
        self.assertEqual(data["pago_prioritario"]["key_orden"], "cuota_inicial")
        self.assertEqual(
            resultado["source_of_truth"],
            "data.credit.flowPayments + data.credit.installments",
        )

        campos_texto = logica.build_pago_text_fields_oficial(resultado)
        self.assertEqual(campos_texto["pago_prioritario_tipo_texto"], "cuota_inicial")
        self.assertEqual(campos_texto["valor_pagar_legible"], "$736.759")

        datos_one2credit = logica.extraer_datos_pagos_mora_one2credit({"data": data})
        self.assertEqual(datos_one2credit["id_cuota_pendiente"], "zero")
        self.assertEqual(datos_one2credit["valor_pagar"], 736759)

    def test_approved_false_deja_cuota_inicial_pendiente(self):
        resultado = self.construir(
            credito_con_cuota_inicial(approved_marker=False)
        )
        self.assertEqual(resultado["data"]["pago_prioritario"]["tipo"], "cuota_inicial")
        self.assertTrue(resultado["data"]["pago_prioritario"]["orden_habilitada"])

    def test_approved_true_no_habilita_otra_orden(self):
        credito = credito_con_cuota_inicial(approved_marker=True)
        credito["flowPayments"][0].update(
            {
                "approvedAt": "2026-07-31T15:35:07Z",
                "orderId": "orden-pagada",
            }
        )
        resultado = self.construir(credito)
        data = resultado["data"]

        self.assertEqual(data["cuota_inicial"]["estado"], "pagada")
        self.assertFalse(data["cuota_inicial"]["orden_habilitada"])
        self.assertIsNone(data["pago_prioritario"])
        self.assertIn(
            "cuota inicial pagada; cuotas pendientes de activacion",
            resultado["alertas_integridad"],
        )

    def test_estados_normales_conservan_pago_pendiente(self):
        credito = credito_con_cuota_inicial(approved_marker=True)
        credito["installments"] = [cuota(1, 1), cuota(2, 3)]
        resultado = self.construir(credito)
        data = resultado["data"]

        self.assertEqual(data["resumen"]["cuotas_pagadas"], 1)
        self.assertEqual(data["resumen"]["pendientes_estado_3"], 1)
        self.assertEqual(data["resumen"]["inactivas_estado_0"], 0)
        self.assertEqual(data["pago_pendiente"]["id"], "cuota-2")
        self.assertEqual(data["pago_prioritario"]["tipo"], "cuota")
        self.assertEqual(data["pago_prioritario"]["id_cuota_orden"], "cuota-2")

    def test_sin_flow_payment_no_crea_prioridad_ni_orden(self):
        credito = credito_con_cuota_inicial(incluir_approved=False)
        credito["flowPayments"] = []
        resultado = self.construir(credito)

        self.assertEqual(resultado["data"]["cuota_inicial"]["estado"], "sin_registro")
        self.assertFalse(resultado["data"]["cuota_inicial"]["orden_habilitada"])
        self.assertIsNone(resultado["data"]["pago_prioritario"])
        self.assertIn(
            "cuotas inactivas sin registro flowPayments para cuota inicial",
            resultado["alertas_integridad"],
        )

    def test_importes_contradictorios_generan_alerta_y_bloquean_orden(self):
        credito = credito_con_cuota_inicial(incluir_approved=False, amount=736759)
        credito["initialFee"] = 700000
        resultado = self.construir(credito)

        self.assertFalse(resultado["data"]["cuota_inicial"]["orden_habilitada"])
        self.assertFalse(resultado["data"]["pago_prioritario"]["orden_habilitada"])
        self.assertIn(
            "flowPayments.cuota_inicial.amount difiere de initialFee",
            resultado["alertas_integridad"],
        )

    def test_importe_flow_payment_invalido_no_usa_fallback_para_crear_orden(self):
        credito = credito_con_cuota_inicial(incluir_approved=False, amount="no-numerico")
        credito["initialFee"] = 736759
        resultado = self.construir(credito)

        self.assertEqual(resultado["data"]["cuota_inicial"]["amount_redondeado"], 736759)
        self.assertFalse(resultado["data"]["cuota_inicial"]["orden_habilitada"])
        self.assertFalse(resultado["data"]["pago_prioritario"]["orden_habilitada"])
        self.assertIn(
            "flowPayments.cuota_inicial.amount no es un importe valido",
            resultado["alertas_integridad"],
        )


class OrdenCuotaInicialTests(unittest.IsolatedAsyncioTestCase):
    async def ejecutar_create_installment_order(self, id_cuota, key=None):
        respuesta_kuenta = SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {"data": {"order": {"meta": {"checkout": "https://checkout"}}}},
        )
        cliente = MagicMock()
        cliente.post = AsyncMock(return_value=respuesta_kuenta)
        context_manager = MagicMock()
        context_manager.__aenter__ = AsyncMock(return_value=cliente)
        context_manager.__aexit__ = AsyncMock(return_value=None)

        with (
            patch.object(logica, "PAYABLE_URL", "https://api.kuenta.co/v1/payables"),
            patch.object(logica.httpx, "AsyncClient", return_value=context_manager),
            patch.object(logica, "obtener_token", new=AsyncMock(return_value="Bearer token")),
            patch.object(logica, "insertar_log", new=AsyncMock()),
        ):
            respuesta = await logica.create_installment_order(
                id_credito_mora="credito-1",
                id_cuota_pendiente=id_cuota,
                payload=logica.InstallmentOrderCreateRequest(
                    amount=736759,
                    provider="payvalida",
                    collectionCosts=0,
                    key=key,
                ),
            )
        return respuesta, cliente.post.await_args

    async def test_orden_inicial_usa_zero_y_key_cuota_inicial(self):
        respuesta, llamada = await self.ejecutar_create_installment_order(
            id_cuota="zero",
            key="cuota_inicial",
        )
        url = llamada.args[0]
        payload = llamada.kwargs["json"]

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(
            url,
            "https://api.kuenta.co/v1/payables/credito-1/installments/zero/orders",
        )
        self.assertEqual(payload["key"], "cuota_inicial")
        self.assertEqual(payload["amount"], 736759)
        self.assertEqual(payload["provider"], "payvalida")
        self.assertEqual(payload["collectionCosts"], 0)
        self.assertIn("date", payload)

    async def test_orden_normal_no_envia_key(self):
        _, llamada = await self.ejecutar_create_installment_order(
            id_cuota="cuota-2",
            key=None,
        )
        self.assertNotIn("key", llamada.kwargs["json"])
        self.assertTrue(llamada.args[0].endswith("/installments/cuota-2/orders"))

    async def test_resolver_cuota_inicial_no_simula_y_crea_valor_completo(self):
        crear_orden = AsyncMock(
            return_value={
                "link_pago": "https://checkout/cuota-inicial",
                "id_orden_pago": "orden-1",
            }
        )
        simular = AsyncMock()
        with (
            patch.object(logica, "crear_orden_pago_one2credit_con_reintentos", new=crear_orden),
            patch.object(logica, "simular_orden_pago_total_one2credit", new=simular),
        ):
            resultado = await logica.resolver_orden_pago_one2credit(
                opcion_pago="pago_total",
                variables_salida={},
                datos_credito={"id_credito_mora": "credito-1"},
                datos_mora={
                    "pago_prioritario_tipo": "cuota_inicial",
                    "id_cuota_pendiente": "zero",
                    "valor_pagar": 736759,
                    "valor_pagar_legible": "$736.759",
                    "orden_habilitada": True,
                },
            )

        simular.assert_not_awaited()
        crear_orden.assert_awaited_once_with(
            id_credito_mora="credito-1",
            id_cuota_pendiente="zero",
            amount=736759,
            key="cuota_inicial",
        )
        self.assertEqual(resultado["opcion_pago"], "cuota_inicial")
        self.assertEqual(resultado["link_pago"], "https://checkout/cuota-inicial")

    async def test_sin_opcion_de_pago_no_crea_orden(self):
        crear_orden = AsyncMock()
        with patch.object(
            logica,
            "crear_orden_pago_one2credit_con_reintentos",
            new=crear_orden,
        ):
            resultado = await logica.resolver_orden_pago_one2credit(
                opcion_pago=None,
                variables_salida={},
                datos_credito={"id_credito_mora": "credito-1"},
                datos_mora={
                    "pago_prioritario_tipo": "cuota_inicial",
                    "id_cuota_pendiente": "zero",
                    "valor_pagar": 736759,
                    "orden_habilitada": True,
                },
            )

        self.assertIsNone(resultado)
        crear_orden.assert_not_awaited()

    async def test_reintentos_propaga_key_y_extrae_checkout(self):
        respuesta = logica.JSONResponse(
            status_code=200,
            content={
                "data": {
                    "order": {
                        "id": "orden-1",
                        "amount": 736759,
                        "meta": {"checkout": "https://checkout/cuota-inicial"},
                    }
                }
            },
        )
        crear = AsyncMock(return_value=respuesta)
        with patch.object(logica, "create_installment_order", new=crear):
            resultado = await logica.crear_orden_pago_one2credit_con_reintentos(
                id_credito_mora="credito-1",
                id_cuota_pendiente="zero",
                amount=736759,
                key="cuota_inicial",
            )

        payload = crear.await_args.kwargs["payload"]
        self.assertEqual(payload.key, "cuota_inicial")
        self.assertEqual(resultado["link_pago"], "https://checkout/cuota-inicial")
        self.assertEqual(resultado["intentos"], 1)


if __name__ == "__main__":
    unittest.main()
