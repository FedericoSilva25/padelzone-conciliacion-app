import pandas as pd
from flask import Flask, request, render_template, redirect, url_for, session
from datetime import timedelta
import os
import json

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'una_clave_secreta_muy_segura_para_desarrollo_local_1234567890abcdef')

UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def conciliar_ventas_banco(df_ventas, df_banco):
    """
    Realiza la conciliación automática entre las ventas y los ingresos bancarios
    con una tolerancia de 24 horas para la fecha.
    """
    # DEBUG: Conteo de filas inicial
    print(f"DEBUG: df_ventas original antes de limpiar columnas: {len(df_ventas)} filas")
    print(f"DEBUG: df_banco original antes de limpiar columnas: {len(df_banco)} filas")

    # Estandarizar nombres de columnas a minúsculas y sin espacios
    df_ventas.columns = df_ventas.columns.str.strip().str.lower()
    df_banco.columns = df_banco.columns.str.strip().str.lower()

    print("Aplicando filtros a las ventas...")

    # Validar si las columnas críticas para el filtro existen después de la estandarización
    if 'cliente' not in df_ventas.columns:
        raise ValueError("Columna 'Cliente' ('cliente') no encontrada en el archivo de ventas después de estandarización. Asegúrate de que el nombre sea correcto.")
    if 'cancelado' not in df_ventas.columns:
        # Si 'cancelado' no existe, aplicamos el filtro solo por cliente
        print("Advertencia: La columna 'cancelado' no se encontró en el archivo de ventas. No se aplicará el filtro por cancelado.")
        df_ventas_filtradas = df_ventas[
            ~df_ventas['cliente'].isin(['academia amateur', 'academia pro'])
        ].copy()
    else:
        # Convertir 'cancelado' a string para manejar posibles variaciones (e.g., espacios)
        df_ventas['cancelado'] = df_ventas['cancelado'].astype(str).str.upper().str.strip()
        df_ventas_filtradas = df_ventas[
            ~df_ventas['cliente'].isin(['academia amateur', 'academia pro']) &
            (df_ventas['cancelado'] != 'SI')
        ].copy()

    # DEBUG: Conteo de filas después de filtros de cliente/cancelado
    print(f"DEBUG: df_ventas_filtradas (después de filtros de cliente/cancelado): {len(df_ventas_filtradas)} filas")


    print("Convirtiendo columnas de fecha y monto a formato estándar...")
    
    # Validar si las columnas de fecha y monto existen después de la estandarización
    if 'fecha pago' not in df_ventas_filtradas.columns:
        raise ValueError("Columna 'Fecha Pago' ('fecha pago') no encontrada en el archivo de ventas después de estandarización. Asegúrate de que el nombre sea correcto.")
    if 'monto' not in df_ventas_filtradas.columns:
        raise ValueError("Columna 'Monto' ('monto') no encontrada en el archivo de ventas después de estandarización. Asegúrate de que el nombre sea correcto.")
    if 'fecha' not in df_banco.columns:
        raise ValueError("Columna 'Fecha' ('fecha') no encontrada en el archivo bancario después de estandarización. Asegúrate de que el nombre sea correcto.")
    if 'importe pesos' not in df_banco.columns:
        raise ValueError("Columna 'Importe Pesos' ('importe pesos') no encontrada en el archivo bancario después de estandarización. Asegúrate de que el nombre sea correcto.")

    # --- INICIO DE AJUSTES CLAVE PARA FECHAS Y MONTOS (AHORA MÁS ROBUSTOS) ---

    # Fechas de ventas: 'DD/MM/YYYY HH:MM:SS'
    df_ventas_filtradas['fecha_estandar'] = pd.to_datetime(df_ventas_filtradas['fecha pago'], format='%d/%m/%Y %H:%M:%S', errors='coerce')

    # Fechas de banco: 'M/D/YYYY' o 'MM/DD/YYYY' (formato US, por lo que dayfirst=False es importante, o no especificarlo y dejar que infiera)
    df_banco['fecha_estandar'] = pd.to_datetime(df_banco['fecha'], errors='coerce', dayfirst=False) 
    

    # Montos de ventas: Limpieza y conversión robusta
    df_ventas_filtradas['monto_estandar'] = (
        df_ventas_filtradas['monto']
        .astype(str)
        .str.replace(r'[^\d,\.-]', '', regex=True) # Elimina todo lo que NO sea dígito, coma, punto o guion
        .str.replace('.', '', regex=False)        # Elimina separadores de miles (punto)
        .str.replace(',', '.', regex=False)        # Reemplaza coma por punto (separador decimal)
    )
    # Convierte a numérico, forzando errores a NaN. Esto es clave para cadenas vacías.
    df_ventas_filtradas['monto_estandar'] = pd.to_numeric(df_ventas_filtradas['monto_estandar'], errors='coerce')


    # Montos de banco: Limpieza y conversión robusta
    df_banco['monto_estandar'] = (
        df_banco['importe pesos']
        .astype(str)
        .str.replace(r'[^\d,\.-]', '', regex=True) # Elimina todo lo que NO sea dígito, coma, punto o guion
        .str.replace('.', '', regex=False)        # Elimina separadores de miles (punto)
        .str.replace(',', '.', regex=False)        # Reemplaza coma por punto (separador decimal)
    )
    # Convierte a numérico, forzando errores a NaN. Esto es clave para cadenas vacías.
    df_banco['monto_estandar'] = pd.to_numeric(df_banco['monto_estandar'], errors='coerce')


    # --- FIN DE AJUSTES CLAVE PARA FECHAS Y MONTOS ---


    # Eliminar filas con fechas o montos inválidos (NaN) después de la conversión
    df_ventas_filtradas.dropna(subset=['fecha_estandar', 'monto_estandar'], inplace=True)
    df_banco.dropna(subset=['fecha_estandar', 'monto_estandar'], inplace=True)

    # DEBUG: Conteo de filas después de limpiar fechas/montos
    print(f"DEBUG: df_ventas_filtradas (después de limpiar fechas/montos): {len(df_ventas_filtradas)} filas")
    print(f"DEBUG: df_banco (después de limpiar fechas/montos): {len(df_banco)} filas")


    print("Iniciando el proceso de conciliación con tolerancia de 24 horas...")
    df_ventas_filtradas['Conciliado'] = False
    df_banco['Conciliado'] = False

    conciliaciones = []

    for idx_venta, row_venta in df_ventas_filtradas.iterrows():
        fecha_min = row_venta['fecha_estandar'] - timedelta(days=1)
        fecha_max = row_venta['fecha_estandar'] + timedelta(days=1)

        # Buscar una transacción en el banco que coincida en fecha (rango) y monto
        # Se asume que solo se concilian ingresos bancarios POSITIVOS con ventas.
        match_banco = df_banco[
            (df_banco['fecha_estandar'] >= fecha_min) &
            (df_banco['fecha_estandar'] <= fecha_max) &
            (df_banco['monto_estandar'] == row_venta['monto_estandar']) &
            (df_banco['monto_estandar'] > 0) & # Asegurarse de que sea un ingreso (monto positivo)
            (~df_banco['Conciliado']) # Solo considerar transacciones no conciliadas
        ]

        if not match_banco.empty:
            idx_banco = match_banco.index[0] # Tomar la primera coincidencia
            df_ventas_filtradas.loc[idx_venta, 'Conciliado'] = True
            df_banco.loc[idx_banco, 'Conciliado'] = True
            conciliaciones.append({
                'Fecha_Venta': row_venta['fecha_estandar'].strftime('%Y-%m-%d %H:%M:%S'), # Mostrar hora en ventas
                'Monto_Venta': row_venta['monto_estandar'],
                'Cliente_Venta': row_venta.get('cliente', 'N/A'),
                'Medio_de_Pago_Venta': row_venta.get('medio de pago', 'N/A'), # Añadir medio de pago
                'Detalle_Venta': row_venta.get('detalle', 'N/A'), # 'detalle' no estaba en la lista de columnas proporcionadas por el usuario para ventas. Se mantiene como 'N/A' si no existe.
                'Fecha_Banco': df_banco.loc[idx_banco, 'fecha_estandar'].strftime('%Y-%m-%d'),
                'Monto_Banco': df_banco.loc[idx_banco, 'monto_estandar'],
                'Concepto_Banco': df_banco.loc[idx_banco].get('concepto', 'N/A') # 'concepto' en minúscula, asumiendo su existencia.
            })

    # Separar transacciones conciliadas de las no conciliadas
    df_conciliado = pd.DataFrame(conciliaciones)
    df_ventas_no_conciliadas = df_ventas_filtradas[~df_ventas_filtradas['Conciliado']].drop(columns=['Conciliado'])
    df_banco_no_conciliado = df_banco[~df_banco['Conciliado']].drop(columns=['Conciliado'])

    print("Conciliación completada.")
    # DEBUG: Conteo final de filas
    print(f"DEBUG: Transacciones conciliadas: {len(df_conciliado)} filas")
    print(f"DEBUG: Ventas NO conciliadas: {len(df_ventas_no_conciliadas)} filas")
    print(f"DEBUG: Ingresos Bancarios NO conciliados: {len(df_banco_no_conciliado)} filas")

    return df_conciliado, df_ventas_no_conciliadas, df_banco_no_conciliado

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        if 'ventas_file' not in request.files or 'banco_file' not in request.files:
            session['message'] = json.dumps({'type': 'error', 'text': 'Ambos archivos son requeridos.'})
            return redirect(request.url)

        ventas_file = request.files['ventas_file']
        banco_file = request.files['banco_file']

        if ventas_file.filename == '' or banco_file.filename == '':
            session['message'] = json.dumps({'type': 'error', 'text': 'Ambos archivos son requeridos.'})
            return redirect(request.url)

        if ventas_file and banco_file:
            ventas_filepath = os.path.join(app.config['UPLOAD_FOLDER'], ventas_file.filename)
            banco_filepath = os.path.join(app.config['UPLOAD_FOLDER'], banco_file.filename)
            ventas_file.save(ventas_filepath)
            banco_file.save(banco_filepath)

            try:
                # Carga de CSVs, especificar 'header=7' para el archivo bancario
                df_ventas = pd.read_csv(ventas_filepath)
                df_banco = pd.read_csv(banco_filepath, header=7) # <-- CAMBIO CLAVE AQUÍ

                df_conciliado, df_ventas_no_conciliadas, df_banco_no_conciliado = conciliar_ventas_banco(df_ventas, df_banco)

                # Asegurarse de que los DataFrames de resultados sean correctos para la visualización HTML
                # Se agregaron las columnas correctas para la conciliación para la visualización
                conciliado_html = df_conciliado.to_html(classes='table table-striped table-bordered', index=False) if not df_conciliado.empty else "<p>No se encontraron transacciones conciliadas.</p>"
                
                # Para Ventas No Conciliadas, mostrar columnas relevantes del archivo de ventas
                ventas_no_conciliadas_cols = ['id. venta', 'fecha pago', 'monto', 'cliente', 'medio de pago', 'cancelado']
                ventas_no_conciliadas_html = df_ventas_no_conciliadas[
                    [col for col in ventas_no_conciliadas_cols if col in df_ventas_no_conciliadas.columns]
                ].to_html(classes='table table-striped table-bordered', index=False) if not df_ventas_no_conciliadas.empty else "<p>Todas las ventas se conciliaron o no hay ventas después de los filtros.</p>"

                # Para Ingresos Bancarios No Conciliados, mostrar columnas relevantes del archivo de banco
                banco_no_conciliado_cols = ['fecha', 'importe pesos', 'concepto', 'suc. origen']
                banco_no_conciliado_html = df_banco_no_conciliado[
                    [col for col in banco_no_conciliado_cols if col in df_banco_no_conciliado.columns]
                ].to_html(classes='table table-striped table-bordered', index=False) if not df_banco_no_conciliado.empty else "<p>Todos los ingresos bancarios se conciliaron o no hay ingresos bancarios.</p>"


                session['message'] = json.dumps({'type': 'success', 'text': 'Conciliación realizada con éxito!'})

                return render_template('index.html',
                                       conciliado=conciliado_html,
                                       ventas_no_conciliadas=ventas_no_conciliadas_html,
                                       banco_no_conciliado=banco_no_conciliado_html
                                       )
            except Exception as e:
                session['message'] = json.dumps({'type': 'error', 'text': f'Error al procesar los archivos: {str(e)}'})
                print(f"Error: {e}")
                return redirect(request.url)
            finally:
                if os.path.exists(ventas_filepath):
                    os.remove(ventas_filepath)
                if os.path.exists(banco_filepath):
                    os.remove(banco_filepath)

    message_from_session = session.pop('message', None)
    return render_template('index.html', message=message_from_session)

if __name__ == '__main__':
    app.run(debug=True)