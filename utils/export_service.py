# utils/export_service.py
import pandas as pd
from io import BytesIO

def generar_excel_memoria(data: list) -> BytesIO:
    """
    Recibe una lista de diccionarios, crea un DataFrame y
    retorna un objeto BytesIO con el archivo Excel (.xlsx).
    """
    try:
        # 1. Crear DataFrame
        df = pd.DataFrame(data)
        
        # 2. Renombrar columnas para que se vean en el Excel (Opcional)
        columnas_mapeo = {
            'nombre_cliente': 'Cliente',
            'cedula': 'Cédula',
            'numero_telefono': 'Teléfono',
            'correo_cliente': 'Correo',
            'fecha_registro': 'Fecha Registro',
            'ID_Credito_simulacion': 'ID Crédito',
            'estado_texto': 'Estado',
            'cuota_inicial_simulacion': 'Cuota Inicial',
            'valor_solicitud_express': 'Valor Solicitud'
        }
        df.rename(columns=columnas_mapeo, inplace=True)

        # 3. Crear el buffer en memoria (RAM)
        output = BytesIO()
        
        # 4. Escribir el Excel usando el motor openpyxl
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Reporte Créditos')
            
            # Ajuste ancho de columnas automáticamente
            worksheet = writer.sheets['Reporte Créditos']
            for column in df:
                column_width = max(df[column].astype(str).map(len).max(), len(column))
                col_idx = df.columns.get_loc(column)
                worksheet.column_dimensions[chr(65 + col_idx)].width = column_width + 2

        # 5. Rebobinar el puntero al inicio del archivo
        output.seek(0)
        return output

    except Exception as e:
        raise Exception(f"Error generando Excel: {str(e)}")