import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

import logica


PLUS_ID = "00be2aee-a04d-4172-9f42-722621a996ac"
PREGRADO_ID = "87e4740b-aad5-48f7-a197-c4d79cb621fb"


def producto_plus():
    return {
        "ID": PLUS_ID,
        "name": "Universidad UNICOSTA Plus",
        "archived": False,
        "principalMin": 150000,
        "principalMax": 9000000,
        "timeMin": 30,
        "timeMax": 150,
        "timeDefault": 150,
        "paymentFrequency": [30],
        "initialFee": False,
        "initialFeeMin": 0,
        "initialFeeMax": 0,
        "initialFeeMinRate": 0,
        "initialFeeMaxRate": 0,
    }


def producto_pregrado():
    return {
        "ID": PREGRADO_ID,
        "name": "Impulsa UNICOSTA PREGRADO",
        "archived": False,
        "principalMin": 150000,
        "principalMax": 20000000,
        "timeMin": 30,
        "timeMax": 150,
        "timeDefault": 150,
        "paymentFrequency": 30,
        "initialFee": True,
        "initialFeeMin": 0,
        "initialFeeMax": 0,
        "initialFeeMinRate": 0.1,
        "initialFeeMaxRate": 0.9,
    }


def payable(
    credit_line_id=PLUS_ID,
    principal=1500000,
    time=150,
    initial_fee=0,
    payment_frequency=30,
):
    return logica.PayableRequest(
        creditLineId=credit_line_id,
        principal=principal,
        time=time,
        initialFee=initial_fee,
        paymentFrequency=payment_frequency,
        disbursementMethod="",
        source="",
        redirectUrl="",
        callbackUrl="",
        meta=None,
    )


def cliente_async_mock():
    cliente = MagicMock()
    context_manager = MagicMock()
    context_manager.__aenter__ = AsyncMock(return_value=cliente)
    context_manager.__aexit__ = AsyncMock(return_value=None)
    return cliente, context_manager


class ProductLineTextFieldsTests(unittest.TestCase):
    def test_contexto_ia_y_json_texto_conservan_respuesta_base(self):
        respuesta_base = {
            "id": PLUS_ID,
            "name": "Universidad ÚNICOSTA Plus",
            "title": "Línea Universitaria",
            "parentId": "parent-1",
            "version": 2,
            "matched_by": "parentId",
            "principalMin": 150000,
            "principalMax": 9000000,
            "timeMin": 30,
            "timeMax": 150,
            "timeDefault": 150,
            "paymentFrequency": [30],
        }

        fields = logica.build_product_line_text_fields(respuesta_base)
        contexto = fields["linea_producto_contexto_ia"]

        self.assertIn("Universidad ÚNICOSTA Plus", contexto)
        self.assertIn("Principal mínimo: 150000 ($150.000 COP)", contexto)
        self.assertIn("Principal máximo: 9000000 ($9.000.000 COP)", contexto)
        self.assertIn("Plazo mínimo: 30 días", contexto)
        self.assertIn("Plazo máximo: 150 días", contexto)
        self.assertIn("Frecuencias de pago permitidas: 30 días", contexto)
        self.assertIn("incluyendo ambos límites", contexto)
        self.assertIn("creditLineId", contexto)
        self.assertEqual(
            json.loads(fields["linea_producto_json_texto"]),
            respuesta_base,
        )
        self.assertNotIn("linea_producto_contexto_ia", fields["linea_producto_json_texto"])

    def test_valores_ausentes_se_explican_sin_alterar_json(self):
        respuesta_base = {
            "id": "linea-1",
            "name": None,
            "title": None,
            "parentId": None,
            "version": None,
            "matched_by": "slug",
            "principalMin": None,
            "principalMax": None,
            "timeMin": None,
            "timeMax": None,
            "timeDefault": None,
            "paymentFrequency": [],
        }
        fields = logica.build_product_line_text_fields(respuesta_base)

        self.assertIn("No informado", fields["linea_producto_contexto_ia"])
        parsed = json.loads(fields["linea_producto_json_texto"])
        self.assertIsNone(parsed["principalMin"])
        self.assertEqual(parsed["paymentFrequency"], [])


class ProductLineEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_endpoint_conserva_campos_y_agrega_variables_para_ia(self):
        _, context_manager = cliente_async_mock()
        external_client = MagicMock()
        external_client.url = "https://api.kuenta.co/v1/product-lines"
        external_client.set_dynamic_values = MagicMock()
        external_client.run = AsyncMock(
            return_value={
                "status": 200,
                "data": {
                    "data": {
                        "lines": [
                            {
                                "id": PLUS_ID,
                                "name": "Universidad ÚNICOSTA Plus",
                                "title": "Línea Universitaria",
                                "parentId": "parent-1",
                                "version": 2,
                                "archived": False,
                                "principalMin": 150000,
                                "principalMax": 9000000,
                                "timeMin": 30,
                                "timeMax": 150,
                                "timeDefault": 150,
                                "paymentFrequency": [30],
                                "updatedAt": "2026-07-27T20:48:15Z",
                            }
                        ]
                    }
                },
            }
        )

        with (
            patch.object(logica.httpx, "AsyncClient", return_value=context_manager),
            patch.object(logica, "obtener_token", new=AsyncMock(return_value="token")),
            patch.object(
                logica.ExternalClient,
                "from_code",
                new=AsyncMock(return_value=external_client),
            ),
        ):
            response = await logica.webhook_product_lines("parent-1")

        self.assertEqual(response["id"], PLUS_ID)
        self.assertEqual(response["principalMin"], 150000)
        self.assertEqual(response["timeMax"], 150)
        self.assertIsInstance(response["linea_producto_contexto_ia"], str)
        self.assertEqual(
            json.loads(response["linea_producto_json_texto"])["id"],
            PLUS_ID,
        )


class PayableRequestContractTests(unittest.TestCase):
    def datos_base(self):
        return {
            "principal": "1500000",
            "time": "150",
            "paymentFrequency": "30",
            "initialFee": "150000",
            "disbursementMethod": "",
            "meta": None,
        }

    def test_acepta_credit_line_id_canonico_y_compatibilidad(self):
        canonical = logica.PayableRequest.model_validate(
            {**self.datos_base(), "creditLineID": PLUS_ID}
        )
        compatible = logica.PayableRequest.model_validate(
            {**self.datos_base(), "creditLineId": PLUS_ID}
        )

        self.assertEqual(canonical.creditLineId, PLUS_ID)
        self.assertEqual(compatible.creditLineId, PLUS_ID)
        self.assertEqual(canonical.disbursementMethod, "")
        self.assertEqual(canonical.meta, {})

    def test_rechaza_aliases_con_ids_diferentes(self):
        with self.assertRaises(logica.ValidationError):
            logica.PayableRequest.model_validate(
                {
                    **self.datos_base(),
                    "creditLineID": PLUS_ID,
                    "creditLineId": PREGRADO_ID,
                }
            )


class PayableProductValidationTests(unittest.TestCase):
    def test_time_180_retorna_invalid_days_antes_de_cuota_inicial(self):
        with self.assertRaises(HTTPException) as raised:
            logica.validar_payable_con_producto(
                payable(time=180, initial_fee=150000),
                producto_plus(),
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail["codigo"], "InvalidDays")
        self.assertEqual(raised.exception.detail["restricciones"]["timeMax"], 150)

    def test_plus_acepta_entrada_con_cuota_para_omitirla_al_enviar(self):
        logica.validar_payable_con_producto(
            payable(time=150, initial_fee=150000),
            producto_plus(),
        )

    def test_plus_valido_y_pregrado_valido(self):
        logica.validar_payable_con_producto(
            payable(time=150, initial_fee=0),
            producto_plus(),
        )
        logica.validar_payable_con_producto(
            payable(
                credit_line_id=PREGRADO_ID,
                principal=1350000,
                time=150,
                initial_fee=150000,
            ),
            producto_pregrado(),
        )

    def test_no_sustituye_una_linea_por_otra_del_mismo_producto(self):
        with self.assertRaises(HTTPException) as raised:
            logica.validar_payable_con_producto(
                payable(credit_line_id=PLUS_ID),
                producto_pregrado(),
            )

        self.assertEqual(raised.exception.detail["codigo"], "InvalidCreditLine")

    def test_frecuencia_y_principal_fuera_de_limite(self):
        with self.assertRaises(HTTPException) as principal_error:
            logica.validar_payable_con_producto(
                payable(principal=100000),
                producto_plus(),
            )
        self.assertEqual(principal_error.exception.detail["codigo"], "InvalidPrincipal")

        with self.assertRaises(HTTPException) as frequency_error:
            logica.validar_payable_con_producto(
                payable(payment_frequency=15),
                producto_plus(),
            )
        self.assertEqual(
            frequency_error.exception.detail["codigo"],
            "InvalidPaymentFrequency",
        )


class CreatePayableFlowTests(unittest.IsolatedAsyncioTestCase):
    async def ejecutar_error_post(self, post_client, producto, request_payload):
        _, context_manager = cliente_async_mock()
        with (
            patch.object(logica.httpx, "AsyncClient", return_value=context_manager),
            patch.object(logica, "obtener_token", new=AsyncMock(return_value="token-secreto")),
            patch.object(
                logica,
                "obtener_producto_kuenta_para_validacion",
                new=AsyncMock(return_value=producto),
            ),
            patch.object(
                logica.ExternalClient,
                "from_code",
                new=AsyncMock(return_value=post_client),
            ),
            patch.object(logica, "error_notify", new=AsyncMock()),
            patch.object(logica, "insertar_log", new=AsyncMock()),
            patch.object(logica.asyncio, "sleep", new=AsyncMock()),
        ):
            return await logica.create_payable("cliente-1", request_payload)

    async def test_invalid_days_no_intenta_crear_payable(self):
        _, context_manager = cliente_async_mock()
        from_code = AsyncMock()
        with (
            patch.object(logica.httpx, "AsyncClient", return_value=context_manager),
            patch.object(logica, "obtener_token", new=AsyncMock(return_value="token")),
            patch.object(
                logica,
                "obtener_producto_kuenta_para_validacion",
                new=AsyncMock(return_value=producto_plus()),
            ),
            patch.object(logica.ExternalClient, "from_code", new=from_code),
            patch.object(logica, "error_notify", new=AsyncMock()),
            patch.object(logica, "insertar_log", new=AsyncMock()),
        ):
            response = await logica.create_payable(
                "cliente-1",
                payable(time=180, initial_fee=150000),
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.body)["codigo"], "InvalidDays")
        from_code.assert_not_awaited()

    async def test_payload_canonico_conserva_principal_y_omite_none(self):
        _, context_manager = cliente_async_mock()
        post_client = MagicMock()
        post_client.url = "https://api.kuenta.co/v1/payables"
        post_client.set_dynamic_values = MagicMock()
        post_client.set_body = MagicMock()
        post_client.set_headers = MagicMock()
        post_client.run = AsyncMock(
            return_value={
                "status": 201,
                "data": {"data": {"credit": {"ID": "credito-1"}}},
            }
        )
        get_client = MagicMock()
        get_client.url = "https://api.kuenta.co/v1/payables"
        get_client.set_path = MagicMock()
        get_client.set_headers = MagicMock()
        get_client.run = AsyncMock(
            return_value={
                "status": 200,
                "data": {
                    "data": {
                        "credit": {
                            "ID": "credito-1",
                            "reference": "REF-1",
                            "debtorID": "cliente-1",
                            "initialFee": 150000,
                            "installments": [
                                {
                                    "payment": 350000,
                                    "capital": 300000,
                                    "interest": 40000,
                                    "costs": 8000,
                                    "taxes": 2000,
                                }
                            ],
                        }
                    }
                },
            }
        )

        async def from_code(code, client_id=None):
            if code == "KUENTA_PAYABLE_CREATE":
                return post_client
            if code == "KUENTA_PAYABLE_GET":
                return get_client
            raise AssertionError(f"Codigo inesperado: {code}")

        request_payload = payable(
            credit_line_id=PREGRADO_ID,
            principal=1350000,
            time=150,
            initial_fee=150000,
        )
        with (
            patch.object(logica.httpx, "AsyncClient", return_value=context_manager),
            patch.object(logica, "obtener_token", new=AsyncMock(return_value="token")),
            patch.object(
                logica,
                "obtener_producto_kuenta_para_validacion",
                new=AsyncMock(return_value=producto_pregrado()),
            ),
            patch.object(logica.ExternalClient, "from_code", new=AsyncMock(side_effect=from_code)),
            patch.object(logica, "error_notify", new=AsyncMock()),
            patch.object(logica, "insertar_log", new=AsyncMock()),
            patch.object(logica, "info_notify", new=AsyncMock()),
        ):
            response = await logica.create_payable("cliente-1", request_payload)

        self.assertEqual(response["status"], "success")
        canonical_payload = post_client.set_body.call_args.args[0]
        self.assertEqual(canonical_payload["creditLineID"], PREGRADO_ID)
        self.assertNotIn("creditLineId", canonical_payload)
        self.assertEqual(canonical_payload["principal"], 1350000)
        self.assertEqual(canonical_payload["initialFee"], 150000)
        self.assertEqual(canonical_payload["disbursementMethod"], "")
        self.assertEqual(canonical_payload["meta"], {})

    async def test_plus_omite_initial_fee_y_simulacion_acepta_valor_ausente(self):
        _, context_manager = cliente_async_mock()
        post_client = MagicMock()
        post_client.url = "https://api.kuenta.co/v1/payables"
        post_client.set_dynamic_values = MagicMock()
        post_client.set_body = MagicMock()
        post_client.set_headers = MagicMock()
        post_client.run = AsyncMock(
            return_value={
                "status": 201,
                "data": {"data": {"credit": {"ID": "credito-plus"}}},
            }
        )
        get_client = MagicMock()
        get_client.url = "https://api.kuenta.co/v1/payables"
        get_client.set_path = MagicMock()
        get_client.set_headers = MagicMock()
        get_client.run = AsyncMock(
            return_value={
                "status": 200,
                "data": {
                    "data": {
                        "credit": {
                            "ID": "credito-plus",
                            "reference": "REF-PLUS",
                            "debtorID": "cliente-1",
                            "installments": [
                                {
                                    "payment": 350000,
                                    "capital": 300000,
                                    "interest": 40000,
                                    "costs": 8000,
                                    "taxes": 2000,
                                }
                            ],
                        }
                    }
                },
            }
        )

        async def from_code(code, client_id=None):
            if code == "KUENTA_PAYABLE_CREATE":
                return post_client
            if code == "KUENTA_PAYABLE_GET":
                return get_client
            raise AssertionError(f"Codigo inesperado: {code}")

        with (
            patch.object(logica.httpx, "AsyncClient", return_value=context_manager),
            patch.object(logica, "obtener_token", new=AsyncMock(return_value="token")),
            patch.object(
                logica,
                "obtener_producto_kuenta_para_validacion",
                new=AsyncMock(return_value=producto_plus()),
            ),
            patch.object(logica.ExternalClient, "from_code", new=AsyncMock(side_effect=from_code)),
            patch.object(logica, "error_notify", new=AsyncMock()),
            patch.object(logica, "insertar_log", new=AsyncMock()),
            patch.object(logica, "info_notify", new=AsyncMock()),
        ):
            response = await logica.create_payable(
                "cliente-1",
                payable(initial_fee=150000),
            )

        canonical_payload = post_client.set_body.call_args.args[0]
        self.assertEqual(response["status"], "success")
        self.assertEqual(canonical_payload["creditLineID"], PLUS_ID)
        self.assertEqual(canonical_payload["principal"], 1500000)
        self.assertNotIn("initialFee", canonical_payload)
        self.assertEqual(
            response["valores_formateados"]["cuota_inicial_formatted"],
            "$0",
        )

    async def test_respuesta_exitosa_sin_id_es_error_controlado_y_no_reintenta(self):
        post_client = MagicMock()
        post_client.url = "https://api.kuenta.co/v1/payables"
        post_client.set_dynamic_values = MagicMock()
        post_client.set_body = MagicMock()
        post_client.set_headers = MagicMock()
        post_client.run = AsyncMock(
            return_value={"status": 201, "data": {"data": {"credit": {}}}}
        )

        response = await self.ejecutar_error_post(
            post_client,
            producto_plus(),
            payable(initial_fee=150000),
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(json.loads(response.body)["codigo"], "InvalidPayableResponse")
        self.assertEqual(post_client.run.await_count, 1)

    async def test_timeout_reintenta_tres_veces_y_devuelve_504(self):
        post_client = MagicMock()
        post_client.url = "https://api.kuenta.co/v1/payables"
        post_client.set_dynamic_values = MagicMock()
        post_client.set_body = MagicMock()
        post_client.set_headers = MagicMock()
        post_client.run = AsyncMock(side_effect=logica.httpx.ReadTimeout("timeout"))

        response = await self.ejecutar_error_post(
            post_client,
            producto_plus(),
            payable(initial_fee=150000),
        )

        self.assertEqual(response.status_code, 504)
        self.assertEqual(post_client.run.await_count, 3)

    async def test_error_5xx_reintenta_tres_veces_sin_exponer_depuracion(self):
        post_client = MagicMock()
        post_client.url = "https://api.kuenta.co/v1/payables"
        post_client.set_dynamic_values = MagicMock()
        post_client.set_body = MagicMock()
        post_client.set_headers = MagicMock()
        post_client.run = AsyncMock(
            return_value={
                "status": 503,
                "data": "<html>servicio no disponible</html>",
            }
        )

        response = await self.ejecutar_error_post(
            post_client,
            producto_plus(),
            payable(initial_fee=150000),
        )
        body = json.loads(response.body)

        self.assertEqual(response.status_code, 502)
        self.assertEqual(post_client.run.await_count, 3)
        self.assertNotIn("depuracion", body)

    async def test_error_400_externo_no_se_reintenta(self):
        _, context_manager = cliente_async_mock()
        post_client = MagicMock()
        post_client.url = "https://api.kuenta.co/v1/payables"
        post_client.set_dynamic_values = MagicMock()
        post_client.set_body = MagicMock()
        post_client.set_headers = MagicMock()
        post_client.run = AsyncMock(
            return_value={
                "status": 400,
                "data": {
                    "data": {
                        "code": "BusinessRule",
                        "message": "Datos no validos",
                    }
                },
            }
        )

        with (
            patch.object(logica.httpx, "AsyncClient", return_value=context_manager),
            patch.object(logica, "obtener_token", new=AsyncMock(return_value="token")),
            patch.object(
                logica,
                "obtener_producto_kuenta_para_validacion",
                new=AsyncMock(return_value=producto_plus()),
            ),
            patch.object(
                logica.ExternalClient,
                "from_code",
                new=AsyncMock(return_value=post_client),
            ),
            patch.object(logica, "error_notify", new=AsyncMock()),
            patch.object(logica, "insertar_log", new=AsyncMock()),
        ):
            response = await logica.create_payable(
                "cliente-1",
                payable(time=150, initial_fee=0),
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.body)["codigo"], "BusinessRule")
        self.assertEqual(post_client.run.await_count, 1)

    async def test_error_409_y_422_no_se_reintentan(self):
        for status in (409, 422):
            with self.subTest(status=status):
                post_client = MagicMock()
                post_client.url = "https://api.kuenta.co/v1/payables"
                post_client.set_dynamic_values = MagicMock()
                post_client.set_body = MagicMock()
                post_client.set_headers = MagicMock()
                post_client.run = AsyncMock(
                    return_value={
                        "status": status,
                        "data": {
                            "data": {
                                "code": "BusinessRule",
                                "message": "Datos no validos",
                            }
                        },
                    }
                )

                response = await self.ejecutar_error_post(
                    post_client,
                    producto_plus(),
                    payable(initial_fee=150000),
                )

                self.assertEqual(response.status_code, status)
                self.assertEqual(post_client.run.await_count, 1)


class CreatePayableSecurityTests(unittest.TestCase):
    def test_authorization_se_redacta_sin_modificar_otros_headers(self):
        headers = {
            "Authorization": "token-secreto",
            "Content-Type": "application/json",
            "organization-id": "org-1",
        }
        safe_headers = logica.sanitizar_depuracion_kuenta(headers)

        self.assertEqual(safe_headers["Authorization"], "***REDACTED***")
        self.assertEqual(safe_headers["Content-Type"], "application/json")
        self.assertEqual(headers["Authorization"], "token-secreto")

        serialized = logica.truncar_depuracion_kuenta(headers)
        self.assertNotIn("token-secreto", serialized)

    def test_respuesta_http_vacia_o_html_se_normaliza_sin_excepcion(self):
        empty = logica.httpx.Response(503, content=b"")
        html = logica.httpx.Response(
            503,
            text="<html>servicio no disponible</html>",
            headers={"content-type": "text/html"},
        )

        empty_data = logica._json_respuesta_http_seguro(empty)
        html_data = logica._json_respuesta_http_seguro(html)

        self.assertEqual(empty_data["code"], "InvalidJSONResponse")
        self.assertEqual(html_data["code"], "InvalidJSONResponse")
        self.assertEqual(html_data["bodyLength"], len(html.content))
        self.assertNotIn("body", html_data)


if __name__ == "__main__":
    unittest.main()
