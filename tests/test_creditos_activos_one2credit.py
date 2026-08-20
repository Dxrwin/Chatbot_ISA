import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

import logica


def construir_credito(credito_id, estado):
    return {
        "ID": credito_id,
        "status": estado,
        "consecutive": 100,
        "reference": f"REF-{credito_id}",
        "parentID": "cliente-1",
        "debtorID": "deudor-1",
        "debtorProfileID": "perfil-1",
        "debtor": {
            "ID": "deudor-1",
            "phone": "+573001234567",
            "email": "cliente@example.com",
        },
        "debtorProfile": {
            "ID": "perfil-1",
            "natural": {
                "firstName": "Cliente",
                "lastName": "Prueba",
                "idType": "1",
                "idNumber": "73149837",
                "email": "cliente@example.com",
            },
        },
        "disbursedAt": "2026-01-10T12:00:00Z",
        "creditLineID": "linea-1",
        "creditLine": {"ID": "linea-1", "title": "Linea universitaria"},
        "principal": 1000000,
        "summary": {
            "total": 1100000,
            "paid": 600000,
            "balance": 500000,
            "capitalPaid": 550000,
            "capitalBalance": 450000,
            "interestBalance": 20000,
            "debtInterestBalance": 10000,
            "penaltyBalance": 20000,
            "debt": 500000,
            "debtDays": 30,
        },
        "installments": [
            {
                "id": f"cuota-{credito_id}",
                "number": 1,
                "status": 3,
                "payment": 500000,
                "valuePaid": 100000,
                "capital": 400000,
                "capitalPaid": 50000,
                "interest": 50000,
                "interestPaid": 20000,
                "additionalInterest": 10000,
                "additionalInterestPaid": 5000,
                "debtPayment": 10000,
                "debtInterest": 10000,
                "debtInterestPaid": 5000,
                "penalty": 10000,
                "penaltyPaid": 5000,
                "costs": 10000,
                "costsPaid": 5000,
                "taxes": 10000,
                "taxesPaid": 5000,
            }
        ],
    }


def construir_consulta(creditos):
    return {
        "credito": creditos[0],
        "creditos": creditos,
        "datos_credito": logica.extraer_datos_credito_mora_one2credit(
            creditos[0]
        ),
        "respuesta_proveedor": {
            "status": "success",
            "data": {"credits": creditos},
        },
    }


class SeleccionCreditosActivosOne2CreditTests(unittest.TestCase):
    def test_estados_activos_son_unicamente_desembolsado_y_mora(self):
        creditos = [
            construir_credito("desembolsado", 7),
            construir_credito("pagado", "8"),
            construir_credito("mora", 10),
            construir_credito("desistido", 9),
            construir_credito("castigado", 16),
        ]

        seleccionados = logica.seleccionar_creditos_activos_one2credit(creditos)

        self.assertEqual(
            [credito["ID"] for credito in seleccionados],
            ["desembolsado", "mora"],
        )

    def test_estado_invalido_no_se_considera_activo(self):
        creditos = [
            construir_credito("sin-estado", None),
            construir_credito("estado-invalido", "desconocido"),
        ]

        self.assertEqual(
            logica.seleccionar_creditos_activos_one2credit(creditos),
            [],
        )


class EndpointCreditosActivosOne2CreditTests(unittest.IsolatedAsyncioTestCase):
    async def test_retorna_dos_activos_y_excluye_creditos_desistidos(self):
        creditos = [
            construir_credito("credito-mora", 10),
            construir_credito("credito-desembolsado", 7),
            construir_credito("credito-desistido-1", 9),
            construir_credito("credito-desistido-2", 9),
        ]

        with patch.object(
            logica,
            "consultar_creditos_one2credit_por_cedula",
            new=AsyncMock(return_value=construir_consulta(creditos)),
        ) as consultar:
            respuesta = await logica.consultar_creditos_activos_one2credit_endpoint(
                " 73149837 "
            )

        consultar.assert_awaited_once_with(
            "73149837",
            estados=[7, 10],
            order="updated_at:desc",
            status_counts=True,
        )
        self.assertEqual(respuesta["tipo_resultado"], "multiples_creditos_activos")
        self.assertEqual(respuesta["total_creditos_consultados"], 4)
        self.assertEqual(respuesta["total_creditos_activos"], 2)
        self.assertIsNone(respuesta["credito_seleccionado"])
        self.assertEqual(
            [item["credito"]["ID"] for item in respuesta["creditos_activos"]],
            ["credito-mora", "credito-desembolsado"],
        )
        self.assertEqual(
            [item["estado"]["nombre"] for item in respuesta["creditos_activos"]],
            ["MORA", "DESEMBOLSADO"],
        )

    async def test_desembolsado_pagado_y_dos_desistidos_retorna_un_solo_activo(self):
        creditos = [
            construir_credito("credito-desembolsado", 7),
            construir_credito("credito-pagado", 8),
            construir_credito("credito-desistido-1", 9),
            construir_credito("credito-desistido-2", 9),
        ]

        with patch.object(
            logica,
            "consultar_creditos_one2credit_por_cedula",
            new=AsyncMock(return_value=construir_consulta(creditos)),
        ):
            respuesta = await logica.consultar_creditos_activos_one2credit_endpoint(
                "1001969573"
            )

        self.assertEqual(respuesta["tipo_resultado"], "credito_activo_unico")
        self.assertEqual(respuesta["total_creditos_activos"], 1)
        self.assertEqual(
            respuesta["credito_seleccionado"]["credito"]["ID"],
            "credito-desembolsado",
        )
        self.assertEqual(
            [item["credito"]["ID"] for item in respuesta["creditos_activos"]],
            ["credito-desembolsado"],
        )

    async def test_un_credito_activo_se_retorna_como_seleccionado(self):
        creditos = [
            construir_credito("credito-desembolsado", 7),
            construir_credito("credito-desistido", 9),
        ]

        with patch.object(
            logica,
            "consultar_creditos_one2credit_por_cedula",
            new=AsyncMock(return_value=construir_consulta(creditos)),
        ):
            respuesta = await logica.consultar_creditos_activos_one2credit_endpoint(
                "73149837"
            )

        seleccionado = respuesta["credito_seleccionado"]
        self.assertEqual(respuesta["tipo_resultado"], "credito_activo_unico")
        self.assertEqual(respuesta["total_creditos_activos"], 1)
        self.assertEqual(seleccionado["credito"]["ID"], "credito-desembolsado")
        self.assertEqual(seleccionado["estado"]["nombre"], "DESEMBOLSADO")
        self.assertEqual(
            seleccionado["credito"]["installments"][0]["id"],
            "cuota-credito-desembolsado",
        )
        resumen = seleccionado["resumen_credito"]
        self.assertEqual(resumen["cliente"]["nombre_completo"], "Cliente Prueba")
        self.assertEqual(resumen["linea_credito"]["nombre"], "Linea universitaria")
        self.assertEqual(resumen["saldo"]["saldo_general"], 500000)
        self.assertEqual(resumen["saldo"]["saldo_general_format"], "500.000")
        self.assertEqual(resumen["saldo"]["total_credito_format"], "1.100.000")
        self.assertEqual(resumen["credito"]["capital_inicial_format"], "1.000.000")
        self.assertEqual(resumen["cuotas"]["total"], 1)
        self.assertEqual(resumen["cuotas"]["pendientes"], 1)
        self.assertEqual(
            resumen["cuotas"]["cuota_pendiente"]["saldo_restante"],
            400000,
        )
        self.assertEqual(
            resumen["cuotas"]["cuota_pendiente"]["saldo_restante_format"],
            "400.000",
        )
        self.assertNotIn(
            "pagos_registrados",
            resumen["cuotas"]["cuota_pendiente"],
        )

    async def test_incluye_pagos_solo_en_cuotas_que_los_tienen(self):
        credito = construir_credito("credito-mora", 10)
        credito["installments"][0]["payments"] = [
            {
                "registeredAt": "2026-01-15T12:00:00Z",
                "orderId": "orden-1",
                "amount": 120000,
                "valuePaid": 100000,
                "capitalPaid": 50000,
                "interestPaid": 20000,
                "additionalInterestPaid": 5000,
                "debtInterestPaid": 5000,
                "penaltyPaid": 5000,
                "costsPaid": 5000,
                "taxesPaid": 5000,
                "isCompletion": False,
            }
        ]

        with patch.object(
            logica,
            "consultar_creditos_one2credit_por_cedula",
            new=AsyncMock(return_value=construir_consulta([credito])),
        ):
            respuesta = await logica.consultar_creditos_activos_one2credit_endpoint(
                "73149837"
            )

        cuota = respuesta["credito_seleccionado"]["resumen_credito"]["cuotas"][
            "cuota_pendiente"
        ]
        self.assertEqual(cuota["cantidad_pagos_registrados"], 1)
        self.assertEqual(cuota["pagos_registrados"][0]["id_orden"], "orden-1")
        self.assertEqual(
            cuota["pagos_registrados"][0]["monto_registrado"],
            120000,
        )
        self.assertEqual(
            cuota["pagos_registrados"][0]["monto_registrado_format"],
            "120.000",
        )
        self.assertEqual(
            cuota["pagos_registrados"][0]["valor_aplicado_cuota"],
            100000,
        )
        self.assertEqual(
            cuota["pagos_registrados"][0]["desglose_aplicado"]["capital_format"],
            "50.000",
        )

    async def test_saldo_restante_nunca_es_negativo_por_redondeo(self):
        credito = construir_credito("credito-desembolsado", 7)
        credito["installments"][0].update(
            {"status": 1, "payment": 683932.92, "valuePaid": 683933}
        )

        with patch.object(
            logica,
            "consultar_creditos_one2credit_por_cedula",
            new=AsyncMock(return_value=construir_consulta([credito])),
        ):
            respuesta = await logica.consultar_creditos_activos_one2credit_endpoint(
                "73149837"
            )

        detalle = respuesta["credito_seleccionado"]["resumen_credito"]["cuotas"][
            "detalle"
        ]
        self.assertEqual(detalle[0]["saldo_restante"], 0)
        self.assertEqual(detalle[0]["saldo_restante_format"], "0")

    async def test_consulta_kuenta_usa_el_mismo_filtro_de_la_plataforma(self):
        credito = construir_credito("credito-desembolsado", 7)
        respuesta_kuenta = SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {
                "status": "success",
                "data": {"credits": [credito], "statusCounts": {"7": 1}},
            },
        )
        cliente = MagicMock()
        cliente.get = AsyncMock(return_value=respuesta_kuenta)
        context_manager = MagicMock()
        context_manager.__aenter__ = AsyncMock(return_value=cliente)
        context_manager.__aexit__ = AsyncMock(return_value=None)

        with (
            patch.object(logica.httpx, "AsyncClient", return_value=context_manager),
            patch.object(logica, "obtener_token", new=AsyncMock(return_value="token")),
            patch.object(logica, "insertar_log", new=AsyncMock()),
        ):
            await logica.consultar_creditos_one2credit_por_cedula(
                "1001969573",
                estados=[7, 10],
                order="updated_at:desc",
                status_counts=True,
            )

        params = cliente.get.await_args.kwargs["params"]
        self.assertEqual(params["offset"], 0)
        self.assertEqual(params["limit"], 10)
        self.assertEqual(params["statusCounts"], "true")
        self.assertEqual(params["include"], "summary,installments")
        self.assertEqual(params["status"], "7,10")
        self.assertEqual(params["q"], "1001969573")
        self.assertEqual(params["order"], "updated_at:desc")

    async def test_sin_creditos_activos_retorna_lista_vacia(self):
        creditos = [
            construir_credito("credito-desistido", 9),
            construir_credito("credito-pendiente", 1),
        ]

        with patch.object(
            logica,
            "consultar_creditos_one2credit_por_cedula",
            new=AsyncMock(return_value=construir_consulta(creditos)),
        ):
            respuesta = await logica.consultar_creditos_activos_one2credit_endpoint(
                "73149837"
            )

        self.assertEqual(respuesta["tipo_resultado"], "sin_creditos_activos")
        self.assertEqual(respuesta["total_creditos_activos"], 0)
        self.assertIsNone(respuesta["credito_seleccionado"])
        self.assertEqual(respuesta["creditos_activos"], [])

    async def test_rechaza_cedula_con_solo_espacios(self):
        with self.assertRaises(HTTPException) as contexto:
            await logica.consultar_creditos_activos_one2credit_endpoint("   ")

        self.assertEqual(contexto.exception.status_code, 422)
        self.assertEqual(
            contexto.exception.detail["tipo_error"],
            "cedula_requerida_one2credit",
        )


if __name__ == "__main__":
    unittest.main()
