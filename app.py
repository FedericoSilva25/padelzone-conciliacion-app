import pandas as pd
from flask import Flask, request, render_template, redirect, url_for, session
from datetime import timedelta
import os
import json # Importar el módulo json

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
    print("Aplicando filtros a las ventas...")

    # --- INICIO DE LA SOLUCIÓN: ESTANDARIZAR NOMBRES DE COLUMNAS AL PRINCIPIO ---
    # Limpiar espacios en blanco de los nombres de las columnas y convertirlos a minúsculas
    df_ventas.columns = df_ventas.columns.str.strip().str.lower()
    df_banco.columns = df_banco.columns.str.strip().str.lower()
    # --- FIN DE LA SOLUCIÓN ---

    # Asegurarse de que las columnas existen antes de filtrar
    # Los nombres de las columnas ahora son en minúsculas
    if 'cliente' not in df_ventas.columns:
        print("Advertencia: La columna 'cliente' no se encontró en el archivo de ventas. No se aplicará el filtro por cliente.")
        df_ventas_filtradas = df_ventas.copy()
    elif 'cancelado' not in df_ventas.columns:
        print("Advertencia: La columna 'cancelado' no se encontró en el archivo de ventas. No se aplicará el filtro por cancelado.")
        df_ventas_filtradas = df_ventas[
            ~df_ventas['cliente'].isin(['academia amateur', 'academia pro']) # Ahora 'academia amateur' y 'academia pro'
        ].copy()
    else:
        df_ventas['cancelado'] = df_ventas['cancelado'].astype(str).str.upper().str.strip()
        df_ventas_filtradas = df_ventas[
            ~df_ventas['cliente'].isin(['academia amateur', 'academia pro']) &
            (df_ventas['cancelado'] != 'SI')
        ].copy()

    print("Convirtiendo columnas de fecha y monto a formato estándar...")
    # --- CAMBIOS CLAVE AQUÍ: Usar los nombres de columna correctos y estandarizados ---
    # Para ventas: 'fecha pago' y 'monto'
    # Para banco: 'fecha' y 'importe pesos'
    
    # Validar si las columnas existen antes de intentar usarlas
    if 'fecha pago' not in df_ventas_filtradas.columns:
        raise ValueError("Columna 'Fecha Pago' no encontrada en el archivo de ventas. Asegúrate de que el nombre sea correcto.")
    if 'monto' not in df_ventas_filtradas.columns:
        raise ValueError("Columna 'Monto' no encontrada en el archivo de ventas. Asegúrate de que el nombre sea correcto.")
    if 'fecha' not in df_banco.columns:
        raise ValueError("Columna 'Fecha' no encontrada en el archivo bancario. Asegúrate de que el nombre sea correcto.")
    if 'importe pesos' not in df_banco.columns:
        raise ValueError("Columna 'Importe Pesos' no encontrada en el archivo bancario. Asegúrate de que el nombre sea correcto.")


    df_ventas_filtradas['fecha_estandar'] = pd.to_datetime(df_ventas_filtradas['fecha pago'], errors='coerce')
    df_banco['fecha_estandar'] = pd.to_datetime(df_banco['fecha'], errors='coerce')

    df_ventas_filtradas.dropna(subset=['fecha_estandar'], inplace=True)
    df_banco.dropna(subset=['fecha_estandar'], inplace=True)

    df_ventas_filtradas['monto_estandar'] = pd.to_numeric(df_ventas_filtradas['monto'], errors='coerce')
    df_banco['monto_estandar'] = pd.to_numeric(df_banco['importe pesos'], errors='coerce') # CAMBIO IMPORTANTE AQUÍ

    df_ventas_filtradas.dropna(subset=['monto_estandar'], inplace=True)
    df_banco.dropna(subset=['monto_estandar'], inplace=True)

    print("Iniciando el proceso de conciliación con tolerancia de 24 horas...")
    df_ventas_filtradas['Conciliado'] = False
    df_banco['Conciliado'] = False

    conciliaciones = []

    for idx_venta, row_venta in df_ventas_filtradas.iterrows():
        fecha_min = row_venta['fecha_estandar'] - timedelta(days=1)
        fecha_max = row_venta['fecha_estandar'] + timedelta(days=1)

        match_banco = df_banco[
            (df_banco['fecha_estandar'] >= fecha_min) &
            (df_banco['fecha_estandar'] <= fecha_max) &
            (df_banco['monto_estandar'] == row_venta['monto_estandar']) & # CAMBIO IMPORTANTE AQUÍ
            (~df_banco['Conciliado'])
        ]

        if not match_banco.empty:
            idx_banco = match_banco.index[0]
            df_ventas_filtradas.loc[idx_venta, 'Conciliado'] = True
            df_banco.loc[idx_banco, 'Conciliado'] = True
            conciliaciones.append({
                'Fecha_Venta': row_venta['fecha_estandar'].strftime('%Y-%m-%d'),
                'Monto_Venta': row_venta['monto_estandar'],
                'Cliente_Venta': row_venta.get('cliente', 'N/A'),
                'Detalle_Venta': row_venta.get('detalle', 'N/A'), # 'detalle' no estaba en la lista de columnas, pero es una suposición segura.
                'Fecha_Banco': df_banco.loc[idx_banco, 'fecha_estandar'].strftime('%Y-%m-%d'),
                'Monto_Banco': df_banco.loc[idx_banco, 'monto_estandar'],
                'Concepto_Banco': df_banco.loc[idx_banco].get('concepto', 'N/A') # 'concepto' en minúscula
            })

    df_conciliado = pd.DataFrame(conciliaciones)
    df_ventas_no_conciliadas = df_ventas_filtradas[~df_ventas_filtradas['Conciliado']].drop(columns=['Conciliado'])
    df_banco_no_conciliado = df_banco[~df_banco['Conciliado']].drop(columns=['Conciliado'])

    print("Conciliación completada.")
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
                # --- CAMBIO IMPORTANTE AQUÍ: Especificar 'header=7' para el archivo bancario ---
                df_ventas = pd.read_csv(ventas_filepath)
                df_banco = pd.read_csv(banco_filepath, header=7) # <-- CAMBIO CLAVE AQUÍ

                df_conciliado, df_ventas_no_conciliadas, df_banco_no_conciliado = conciliar_ventas_banco(df_ventas, df_banco)

                conciliado_html = df_conciliado.to_html(classes='table table-striped table-bordered', index=False) if not df_conciliado.empty else "<p>No se encontraron transacciones conciliadas.</p>"
                ventas_no_conciliadas_html = df_ventas_no_conciliadas.to_html(classes='table table-striped table-bordered', index=False) if not df_ventas_no_conciliadas.empty else "<p>Todas las ventas se conciliaron o no hay ventas después de los filtros.</p>"
                banco_no_conciliado_html = df_banco_no_conciliado.to_html(classes='table table-striped table-bordered', index=False) if not df_banco_no_conciliado.empty else "<p>Todos los ingresos bancarios se conciliaron o no hay ingresos bancarios.</p>"

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