import pandas as pd
from flask import Flask, request, render_template, redirect, url_for, session
from datetime import timedelta
import os
import json # Importar el módulo json

app = Flask(__name__)
# ¡IMPORTANTE! Cambia esta clave secreta por una cadena larga, aleatoria y segura.
# En producción, obtén esto de una variable de entorno como se explica en los pasos de Render.
app.secret_key = os.environ.get('SECRET_KEY', 'una_clave_secreta_por_defecto_muy_segura_para_desarrollo')

# Configuración de la carpeta para guardar archivos temporales
UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def conciliar_ventas_banco(df_ventas, df_banco):
    """
    Realiza la conciliación automática entre las ventas y los ingresos bancarios
    con una tolerancia de 24 horas para la fecha.

    Args:
        df_ventas (pd.DataFrame): DataFrame con los datos de ventas.
        df_banco (pd.DataFrame): DataFrame con los datos de ingresos bancarios.

    Returns:
        tuple: Una tupla que contiene:
            - df_conciliado (pd.DataFrame): DataFrame con las transacciones conciliadas.
            - df_ventas_no_conciliadas (pd.DataFrame): DataFrame con las ventas no conciliadas.
            - df_banco_no_conciliado (pd.DataFrame): DataFrame con los ingresos bancarios no conciliados.
    """
    print("Aplicando filtros a las ventas...")
    # Asegurarse de que las columnas existen antes de filtrar
    if 'Cliente' not in df_ventas.columns:
        print("Advertencia: La columna 'Cliente' no se encontró en el archivo de ventas. No se aplicará el filtro por cliente.")
        df_ventas_filtradas = df_ventas.copy()
    elif 'Cancelado' not in df_ventas.columns:
        print("Advertencia: La columna 'Cancelado' no se encontró en el archivo de ventas. No se aplicará el filtro por cancelado.")
        df_ventas_filtradas = df_ventas[
            ~df_ventas['Cliente'].isin(['ACADEMIA AMATEUR', 'ACADEMIA PRO'])
        ].copy()
    else:
        # Convertir 'Cancelado' a string para manejar posibles variaciones (e.g., espacios)
        df_ventas['Cancelado'] = df_ventas['Cancelado'].astype(str).str.upper().str.strip()
        df_ventas_filtradas = df_ventas[
            ~df_ventas['Cliente'].isin(['ACADEMIA AMATEUR', 'ACADEMIA PRO']) &
            (df_ventas['Cancelado'] != 'SI')
        ].copy()

    print("Convirtiendo columnas de fecha a formato estándar...")
    df_ventas_filtradas['Fecha'] = pd.to_datetime(df_ventas_filtradas['Fecha'], errors='coerce')
    df_banco['Fecha'] = pd.to_datetime(df_banco['Fecha'], errors='coerce')

    # Eliminar filas con fechas inválidas después de la conversión
    df_ventas_filtradas.dropna(subset=['Fecha'], inplace=True)
    df_banco.dropna(subset=['Fecha'], inplace=True)

    # Asegurarse de que los montos son numéricos
    df_ventas_filtradas['Monto'] = pd.to_numeric(df_ventas_filtradas['Monto'], errors='coerce')
    df_banco['Monto'] = pd.to_numeric(df_banco['Monto'], errors='coerce')

    # Eliminar filas con montos inválidos después de la conversión
    df_ventas_filtradas.dropna(subset=['Monto'], inplace=True)
    df_banco.dropna(subset=['Monto'], inplace=True)

    print("Iniciando el proceso de conciliación con tolerancia de 24 horas...")
    df_ventas_filtradas['Conciliado'] = False
    df_banco['Conciliado'] = False

    conciliaciones = []

    # Iterar sobre las ventas para encontrar coincidencias en el banco
    for idx_venta, row_venta in df_ventas_filtradas.iterrows():
        # Rango de fechas para la conciliación (fecha_venta - 1 día a fecha_venta + 1 día)
        fecha_min = row_venta['Fecha'] - timedelta(days=1)
        fecha_max = row_venta['Fecha'] + timedelta(days=1)

        match_banco = df_banco[
            (df_banco['Fecha'] >= fecha_min) &
            (df_banco['Fecha'] <= fecha_max) &
            (df_banco['Monto'] == row_venta['Monto']) &
            (~df_banco['Conciliado']) # Solo considerar transacciones no conciliadas
        ]

        if not match_banco.empty:
            idx_banco = match_banco.index[0] # Tomar la primera coincidencia
            df_ventas_filtradas.loc[idx_venta, 'Conciliado'] = True
            df_banco.loc[idx_banco, 'Conciliado'] = True
            conciliaciones.append({
                'Fecha_Venta': row_venta['Fecha'].strftime('%Y-%m-%d'),
                'Monto_Venta': row_venta['Monto'],
                'Cliente_Venta': row_venta.get('Cliente', 'N/A'),
                'Detalle_Venta': row_venta.get('Detalle', 'N/A'),
                'Fecha_Banco': df_banco.loc[idx_banco, 'Fecha'].strftime('%Y-%m-%d'),
                'Monto_Banco': df_banco.loc[idx_banco, 'Monto'],
                'Concepto_Banco': df_banco.loc[idx_banco].get('Concepto', 'N/A')
            })

    # Separar transacciones conciliadas de las no conciliadas
    df_conciliado = pd.DataFrame(conciliaciones)
    df_ventas_no_conciliadas = df_ventas_filtradas[~df_ventas_filtradas['Conciliado']].drop(columns=['Conciliado'])
    df_banco_no_conciliado = df_banco[~df_banco['Conciliado']].drop(columns=['Conciliado'])

    print("Conciliación completada.")
    return df_conciliado, df_ventas_no_conciliadas, df_banco_no_conciliado

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        if 'ventas_file' not in request.files or 'banco_file' not in request.files:
            # Serializar el mensaje de error directamente aquí
            session['message'] = json.dumps({'type': 'error', 'text': 'Ambos archivos son requeridos.'})
            return redirect(request.url)

        ventas_file = request.files['ventas_file']
        banco_file = request.files['banco_file']

        if ventas_file.filename == '' or banco_file.filename == '':
            # Serializar el mensaje de error directamente aquí
            session['message'] = json.dumps({'type': 'error', 'text': 'Ambos archivos son requeridos.'})
            return redirect(request.url)

        if ventas_file and banco_file:
            # Guardar archivos temporales
            ventas_filepath = os.path.join(app.config['UPLOAD_FOLDER'], ventas_file.filename)
            banco_filepath = os.path.join(app.config['UPLOAD_FOLDER'], banco_file.filename)
            ventas_file.save(ventas_filepath)
            banco_file.save(banco_filepath)

            try:
                df_ventas = pd.read_csv(ventas_filepath)
                df_banco = pd.read_csv(banco_filepath)

                df_conciliado, df_ventas_no_conciliadas, df_banco_no_conciliado = conciliar_ventas_banco(df_ventas, df_banco)

                # Convertir DataFrames a HTML para pasarlos a la plantilla
                conciliado_html = df_conciliado.to_html(classes='table table-striped table-bordered', index=False) if not df_conciliado.empty else "<p>No se encontraron transacciones conciliadas.</p>"
                ventas_no_conciliadas_html = df_ventas_no_conciliadas.to_html(classes='table table-striped table-bordered', index=False) if not df_ventas_no_conciliadas.empty else "<p>Todas las ventas se conciliaron o no hay ventas después de los filtros.</p>"
                banco_no_conciliado_html = df_banco_no_conciliado.to_html(classes='table table-striped table-bordered', index=False) if not df_banco_no_conciliado.empty else "<p>Todos los ingresos bancarios se conciliaron o no hay ingresos bancarios.</p>"

                # Serializar el mensaje de éxito directamente aquí
                session['message'] = json.dumps({'type': 'success', 'text': 'Conciliación realizada con éxito!'})

                return render_template('index.html',
                                       conciliado=conciliado_html,
                                       ventas_no_conciliadas=ventas_no_conciliadas_html,
                                       banco_no_conciliado=banco_no_conciliado_html,
                                       # message=session.pop('message', None)  <-- 'message' ya está en session, no lo pop aquí
                                       # Simplemente se pasa a la plantilla y se limpia en el JavaScript
                                       )
            except Exception as e:
                # Serializar el mensaje de error directamente aquí
                session['message'] = json.dumps({'type': 'error', 'text': f'Error al procesar los archivos: {str(e)}'})
                print(f"Error: {e}") # Para depuración en la consola
                return redirect(request.url)
            finally:
                # Opcional: Eliminar archivos temporales después de procesar
                if os.path.exists(ventas_filepath):
                    os.remove(ventas_filepath)
                if os.path.exists(banco_filepath):
                    os.remove(banco_filepath)

    # Para la carga inicial de la página o después de un redirect, obtenemos el mensaje de la sesión
    # y lo pasamos a la plantilla. Si no hay, será None.
    # El JavaScript se encargará de leerlo del atributo data-message del body.
    message_from_session = session.pop('message', None)
    return render_template('index.html', message=message_from_session)

if __name__ == '__main__':
    app.run(debug=True) # debug=True permite ver cambios sin reiniciar el servidor y recargar errores