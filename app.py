from flask import Flask, request, jsonify, send_file 
import qrcode
from io import BytesIO
import psycopg2
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, auth
import os
from dotenv import load_dotenv
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import date, time, datetime


app = Flask(__name__)
CORS(app)

load_dotenv()

# Firebase configuration
cred = credentials.Certificate("Estacionamiento/contra/firebase-service-account.json")
firebase_admin.initialize_app(cred)

# Database connection
def get_db_connection():
    conn = psycopg2.connect(
        host=os.getenv('DB_HOST'),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD')
    )
    return conn

from datetime import datetime, timedelta




# User authentication and QR generation
@app.route('/api/generate_qr', methods=['POST'])
def generate_qr():
    conn = None
    cur = None
    try:
        data = request.json or {}
        token = data.get('token')
        user_type_key = data.get('type')   # 'student' | 'teacher' | 'admin'

        if not token:
            return jsonify({'error': 'Token is required'}), 400

        # 1) Verificar token y extraer email
        decoded_token = auth.verify_id_token(token)
        email = decoded_token['email']

        # 2) Conectar a la base de datos y buscar al usuario
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT id_usuario, tipo_usuario FROM usuario WHERE correo = %s",
            (email,)
        )
        row = cur.fetchone()

        # Mapa de claves a texto para la columna tipo_usuario
        role_map = {
            'student': 'Estudiante',
            'teacher': 'Docente',
            'admin':   'Administrativo'
        }

        # 3) Si no existe, crear nuevo registro con el tipo pasado desde el front
        if not row:
            tipo_usuario = role_map.get(user_type_key, 'Estudiante')

            # Generar nuevo id_usuario (U00001, U00002, …)
            cur.execute("SELECT MAX(CAST(SUBSTRING(id_usuario, 2) AS INTEGER)) FROM usuario;")
            last_id = cur.fetchone()[0] or 0
            new_id = f"U{str(last_id + 1).zfill(5)}"

            cur.execute(
                "INSERT INTO usuario (id_usuario, correo, tipo_usuario) VALUES (%s, %s, %s)",
                (new_id, email, tipo_usuario)
            )
            conn.commit()

            user_id, user_type = new_id, tipo_usuario
        else:
            user_id, user_type = row

        # 4) Revisar si ya existe un QR vigente
        cur.execute("""
            SELECT id_qr
              FROM codigoqr
             WHERE id_usuario = %s
               AND fecha_expiracion >= CURRENT_DATE
             ORDER BY fecha_generacion DESC
             LIMIT 1
        """, (user_id,))
        existing = cur.fetchone()

        if existing:
            qr_id = existing[0]
        else:
            # Generar siguiente id_qr
            cur.execute("SELECT MAX(id_qr) FROM codigoqr;")
            last_qr = cur.fetchone()[0]
            if last_qr and last_qr[2:].isdigit():
                next_num = int(last_qr[2:]) + 1
                qr_id = f"QR{str(next_num).zfill(5)}"
            else:
                qr_id = "QR00001"

            gen_date = datetime.now().date()
            exp_date = gen_date + timedelta(days=30)

            cur.execute("""
                INSERT INTO codigoqr (id_qr, id_usuario, fecha_generacion, fecha_expiracion)
                VALUES (%s, %s, %s, %s)
            """, (qr_id, user_id, gen_date, exp_date))
            conn.commit()

        # 5) Generar imagen del QR
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(qr_id)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        img_io = BytesIO()
        img.save(img_io, 'PNG')
        img_io.seek(0)

        return send_file(img_io, mimetype='image/png')

    except Exception as e:
        return jsonify({'error': str(e)}), 500

    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


# Guard routes
@app.route('/api/guard/login', methods=['POST'])
def guard_login():
    data = request.json
    guard_id = data.get('guard_id')
    name = data.get('name')
    shift = data.get('shift')
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Verify guard exists
        cur.execute("SELECT nombre FROM guardia WHERE id_guardia = %s", (guard_id,))
        guard = cur.fetchone()
        
        if not guard:
            return jsonify({'error': 'Invalid guard ID'}), 401
            
        # Update guard info if needed
        if name or shift:
            cur.execute("""
                UPDATE guardia 
                SET nombre = COALESCE(%s, nombre), turno = COALESCE(%s, turno)
                WHERE id_guardia = %s
            """, (name, shift, guard_id))
            conn.commit()
            
        return jsonify({'success': True, 'guard_id': guard_id})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/api/validate_qr', methods=['POST'])
def validate_qr():
    conn = None
    cur = None
    try:
        data = request.get_json() or {}
        qr_id    = data.get('qr_id')
        guard_id = data.get('guard_id')  # opcional, por si quieres registrar quién valida

        if not qr_id:
            return jsonify({'valid': False, 'error': 'QR ID is required'}), 400

        conn = get_db_connection()
        cur = conn.cursor()

        # 1) Busca el QR y su fecha de expiración
        cur.execute("""
            SELECT fecha_expiracion
              FROM codigoqr
             WHERE id_qr = %s
        """, (qr_id,))
        row = cur.fetchone()

        if not row:
            # No existe ese código
            return jsonify({'valid': False, 'error': 'Código QR no encontrado'}), 404

        fecha_exp = row[0]
        hoy       = datetime.now().date()

        if fecha_exp < hoy:
            # Ya expiró
            return jsonify({
              'valid': False,
              'error': 'Código QR caducado',
              'expired_on': fecha_exp.isoformat()
            }), 410  # 410 Gone indica recurso expirado/eliminado

        # Si llegas aquí, el QR es válido y vigente
        return jsonify({'valid': True}), 200

    except Exception as e:
        return jsonify({'valid': False, 'error': str(e)}), 500

    finally:
        if cur: cur.close()
        if conn: conn.close()

@app.route('/api/record_access', methods=['POST'])
def record_access():
    conn = get_db_connection()
    cur  = conn.cursor()
    try:
        qr_id    = request.json.get('qr_id')
        guard_id = request.json.get('guard_id')
        if not qr_id or not guard_id:
            return jsonify({'error': 'Faltan QR o guardia'}), 400
        
        # 0) Cerrar accesos abiertos de días anteriores (antes de registrar el acceso de hoy)
        cur.execute("""
        UPDATE acceso
        SET hora_salida = time '23:59'
        WHERE fecha < CURRENT_DATE
        AND hora_salida IS NULL
        """)

        # 1) Cierro acceso previo abierto de este QR, si lo hay
        cur.execute("""
            UPDATE acceso
               SET hora_salida = CURRENT_TIME
             WHERE id_qr = %s
               AND fecha = CURRENT_DATE
               AND hora_salida IS NULL
        """, (qr_id,))

        # 2) Inserto nuevo acceso
        cur.execute("""
            INSERT INTO acceso (fecha, hora_entrada, id_guardia, id_qr)
            VALUES (CURRENT_DATE, CURRENT_TIME, %s, %s)
            RETURNING id_acceso
        """, (guard_id, qr_id))
        new_id = cur.fetchone()[0]

        # 3) Actualizo estacionamiento (igual que antes)…
        cur.execute("""
            UPDATE estacionamiento
               SET espacios_disponibles = espacios_disponibles - 1,
                   espacios_ocupados   = espacios_ocupados + 1,
                   capacidad_total     = 100 - ((espacios_disponibles - 1) * 100.0/2000)
             WHERE fecha = CURRENT_DATE
        """)
        if cur.rowcount == 0:
            # inserto el primer registro del día si no existía
            espacios_disp = 2000 - 1
            espacios_occ  = 1
            capacidad     = 100 - (espacios_disp * 100.0 / 2000)
            cur.execute("""
                INSERT INTO estacionamiento (fecha, capacidad_total, espacios_disponibles, espacios_ocupados)
                VALUES (CURRENT_DATE, %s, %s, %s)
            """, (capacidad, espacios_disp, espacios_occ))

        conn.commit()
        return jsonify({'success': True, 'id_acceso': new_id})

    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/record_exit', methods=['POST'])
def record_exit():
    conn = get_db_connection()
    cur  = conn.cursor()
    try:
        qr_id    = request.json.get('qr_id')
        guard_id = request.json.get('guard_id')
        if not qr_id or not guard_id:
            return jsonify({'error': 'Faltan QR o guardia'}), 400

        # Cierro el acceso pendiente
        cur.execute("""
            UPDATE acceso
               SET hora_salida = CURRENT_TIME
             WHERE id_qr = %s
               AND fecha = CURRENT_DATE
               AND hora_salida IS NULL
            RETURNING id_acceso
        """, (qr_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'Acceso no encontrado o ya cerrado'}), 404

        # Incremento espacios disponibles en parking
        cur.execute("""
            UPDATE estacionamiento
               SET espacios_disponibles = espacios_disponibles + 1,
                   capacidad_total     = 100 - ((espacios_disponibles + 1) * 100.0/2000)
             WHERE fecha = CURRENT_DATE
        """)
        conn.commit()
        return jsonify({'success': True, 'id_acceso': row[0]})

    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()




@app.route('/api/register/visitor', methods=['POST'])
def register_visitor():
    data = request.get_json()
    name = data.get('name')
    reason = data.get('reason')
    plate = data.get('plate')
    guard_id = data.get('guard_id')

    if not all([name, reason, guard_id]):
        return jsonify({'error': 'Faltan campos requeridos'}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Generar ID de visitante
        cur.execute("SELECT MAX(SUBSTRING(id_visitante FROM 2)::integer) FROM visitante")
        max_id = cur.fetchone()[0] or 0
        visitor_id = f"V{str(max_id + 1).zfill(5)}"

        # 0) Cerrar accesos abiertos de días anteriores (antes de registrar el acceso de hoy)
        cur.execute("""
            UPDATE acceso
               SET hora_salida = time '23:59'
             WHERE fecha < CURRENT_DATE
               AND hora_salida IS NULL
        """)


        # Insertar visitante
        cur.execute(""" 
            INSERT INTO visitante (id_visitante, nombre, motivo, placa, id_guardia)
            VALUES (%s, %s, %s, %s, %s)
        """, (visitor_id, name, reason, plate, guard_id))

        # Registrar acceso
        cur.execute(""" 
            INSERT INTO acceso (fecha, id_guardia, id_visitante, hora_entrada)
            VALUES (CURRENT_DATE, %s, %s, CURRENT_TIME)
            RETURNING id_acceso
        """, (guard_id, visitor_id))
        new_id = cur.fetchone()[0]

        # Actualizar estacionamiento
        cur.execute("""
            UPDATE estacionamiento
               SET espacios_disponibles = espacios_disponibles - 1,
                   espacios_ocupados   = espacios_ocupados + 1,
                   capacidad_total     = 100 - ((espacios_disponibles - 1) * 100.0/2000)
             WHERE fecha = CURRENT_DATE
        """)
        if cur.rowcount == 0:
            espacios_disp = 2000 - 1
            espacios_occ  = 1
            capacidad     = 100 - (espacios_disp * 100.0 / 2000)
            cur.execute("""
                INSERT INTO estacionamiento (fecha, capacidad_total, espacios_disponibles, espacios_ocupados)
                VALUES (CURRENT_DATE, %s, %s, %s)
            """, (capacidad, espacios_disp, espacios_occ))
        conn.commit()
        return jsonify({'success': True, 'visitor_id': visitor_id, 'id_acceso': new_id})

    except Exception as e:
        conn.rollback()
        print('ERROR EN REGISTER VISITOR:', str(e))
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/api/visitor/exit', methods=['POST'])
def visitor_exit():
    data = request.get_json()
    search_id = data.get('visitor_id')
    plate = data.get('plate')
    guard_id = data.get('guard_id')
    if not guard_id:
        return jsonify({'error': 'Falta id_guardia'}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Buscar visitante por id o placa en accesos abiertos del día
        if search_id:
            cur.execute("""
                SELECT a.id_acceso, v.id_visitante FROM acceso a
                JOIN visitante v ON a.id_visitante = v.id_visitante
                WHERE a.fecha = CURRENT_DATE
                  AND a.hora_salida IS NULL
                  AND v.id_visitante = %s
            """, (search_id,))
        elif plate:
            cur.execute("""
                SELECT a.id_acceso, v.id_visitante FROM acceso a
                JOIN visitante v ON a.id_visitante = v.id_visitante
                WHERE a.fecha = CURRENT_DATE
                  AND a.hora_salida IS NULL
                  AND v.placa = %s
            """, (plate,))
        else:
            return jsonify({'error': 'Debes ingresar id_visitante o placa'}), 400

        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'No se encontró visitante para salida hoy'}), 404

        id_acceso, _ = row
        cur.execute("""
            UPDATE acceso
               SET hora_salida = CURRENT_TIME
             WHERE id_acceso = %s
        """, (id_acceso,))

        # Actualiza estacionamiento
        cur.execute("""
            UPDATE estacionamiento
               SET espacios_disponibles = espacios_disponibles + 1,
                   capacidad_total     = 100 - ((espacios_disponibles + 1) * 100.0/2000)
             WHERE fecha = CURRENT_DATE
        """)

        conn.commit()
        return jsonify({'success': True, 'id_acceso': id_acceso})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/visitor/today', methods=['GET'])
def visitors_today():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT v.id_visitante, v.nombre, v.motivo, v.placa,
                   a.hora_entrada, a.hora_salida
              FROM visitante v
              JOIN acceso a ON v.id_visitante = a.id_visitante
             WHERE a.fecha = CURRENT_DATE
             ORDER BY a.hora_entrada ASC
        """)
        results = [
            {
                'id_visitante': row[0],
                'nombre': row[1],
                'motivo': row[2],
                'placa': row[3],
                'hora_entrada': str(row[4]),
                'hora_salida': str(row[5]) if row[5] else ''
            }
            for row in cur.fetchall()
        ]
        return jsonify(results)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()




# Admin routes
@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    username = request.json.get('username')
    password = request.json.get('password')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT password, role FROM admin_users WHERE username=%s", (username,))
        user = cur.fetchone()
        if not user:
            return jsonify({'error': 'Usuario o contraseña incorrectos'}), 401
        pw_hash, role = user
        if not check_password_hash(pw_hash, password):
            return jsonify({'error': 'Usuario o contraseña incorrectos'}), 401
        return jsonify({'success': True, 'role': role})
    finally:
        cur.close()
        conn.close()

@app.route('/api/admin/change_guardias_password', methods=['POST'])
def change_guardias_password():
    admin_user = request.json.get('admin_username')
    admin_pass = request.json.get('admin_password')
    new_password = request.json.get('new_password')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Verificar si es jefe
        cur.execute("SELECT password, role FROM admin_users WHERE username=%s", (admin_user,))
        user = cur.fetchone()
        if not user or user[1] != 'jefe':
            return jsonify({'error': 'No autorizado'}), 403
        if not check_password_hash(user[0], admin_pass):
            return jsonify({'error': 'No autorizado'}), 403

        new_pw_hash = generate_password_hash(new_password)
        cur.execute("UPDATE admin_users SET password=%s WHERE role='jefe_guardias'", (new_pw_hash,))
        conn.commit()
        return jsonify({'success': True})
    finally:
        cur.close()
        conn.close()


@app.route('/api/report/monthly', methods=['GET'])
def get_monthly_report():
    year = int(request.args.get('year'))
    month = int(request.args.get('month'))
    role = request.args.get('role', '')  # jefe o jefe_guardias

    if role not in ('jefe', 'jefe_guardias'):
        return jsonify({'error': 'No autorizado'}), 403

    report_id = f"R{year}{str(month).zfill(2)}"
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Calcular la suma actual de espacios_ocupados del mes
        cur.execute("""
            SELECT SUM(espacios_ocupados)
            FROM estacionamiento
            WHERE EXTRACT(YEAR FROM fecha) = %s AND EXTRACT(MONTH FROM fecha) = %s
        """, (year, month))
        suma = cur.fetchone()[0] or 0

        # Checa si ya existe el reporte
        cur.execute("SELECT COUNT(*) FROM reportemensual WHERE id_reporte=%s", (report_id,))
        exists = cur.fetchone()[0]

        if exists:
            # Actualiza el total_ingresos si ya existe
            cur.execute("""
                UPDATE reportemensual
                SET total_ingresos = %s
                WHERE id_reporte = %s
            """, (suma, report_id))
            conn.commit()
            total = suma
        else:
            # Da de alta el reporte
            cur.execute("""
                INSERT INTO reportemensual (id_reporte, anio, mes, total_ingresos)
                VALUES (%s, %s, %s, %s)
            """, (report_id, year, month, suma))
            conn.commit()
            total = suma
        return jsonify({'total_ingresos': total, 'report_id': report_id})
    finally:
        cur.close()
        conn.close()



@app.route('/api/report/daily', methods=['GET'])
def get_daily_report():
    date = request.args.get('date')  # YYYY-MM-DD
    role = request.args.get('role', '')
    if role not in ('jefe', 'jefe_guardias'):
        return jsonify({'error': 'No autorizado'}), 403

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT espacios_ocupados FROM estacionamiento WHERE fecha=%s
        """, (date,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'No data for this day'}), 404
        espacios_ocupados = row[0]
        porcentaje = round(espacios_ocupados * 100.0 / 2000, 2)
        return jsonify({
            'espacios_ocupados': espacios_ocupados,
            'porcentaje_ocupacion': porcentaje
        })
    finally:
        cur.close()
        conn.close()

@app.route('/api/report/full_month_access', methods=['GET'])
def get_full_month_access():
    year = int(request.args.get('year'))
    month = int(request.args.get('month'))
    role = request.args.get('role', '')
    if role != 'jefe':
        return jsonify({'error': 'No autorizado'}), 403

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Busca todos los accesos del mes, con datos relacionados (usuario, visitante, guardia)
        cur.execute("""
            SELECT 
                a.*, 
                u.correo, 
                u.tipo_usuario, 
                v.nombre AS visitante_nombre, 
                v.placa, 
                g.nombre AS guardia_nombre, 
                g.turno
            FROM acceso a
            LEFT JOIN codigoqr q ON a.id_qr = q.id_qr
            LEFT JOIN usuario u ON q.id_usuario = u.id_usuario
            LEFT JOIN visitante v ON a.id_visitante = v.id_visitante
            LEFT JOIN guardia g ON a.id_guardia = g.id_guardia
            WHERE EXTRACT(YEAR FROM a.fecha) = %s AND EXTRACT(MONTH FROM a.fecha) = %s
            ORDER BY a.fecha, a.hora_entrada
        """, (year, month))

        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description]

        def serialize(obj):
            if isinstance(obj, (datetime, date, time)):
                return obj.isoformat()
            return obj

        result = []
        for row in rows:
            row_dict = {}
            for k, v in zip(columns, row):
                row_dict[k] = serialize(v)
            result.append(row_dict)
        return jsonify(result)
    finally:
        cur.close()
        conn.close()


scheduler = BackgroundScheduler(timezone='America/Mexico_City')


def create_monthly_report():
    today = datetime.now()
    if today.day == 1:
        last_month = (today.replace(day=1) - timedelta(days=1))
        year, month = last_month.year, last_month.month
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                report_id = f"R{year}{str(month).zfill(2)}"
                cur.execute("SELECT COUNT(*) FROM reportemensual WHERE id_reporte=%s", (report_id,))
                if cur.fetchone()[0] == 0:
                    cur.execute("""
                        SELECT SUM(espacios_ocupados)
                        FROM estacionamiento
                        WHERE EXTRACT(YEAR FROM fecha) = %s AND EXTRACT(MONTH FROM fecha) = %s
                    """, (year, month))
                    suma = cur.fetchone()[0] or 0
                    cur.execute("""
                        INSERT INTO reportemensual (id_reporte, anio, mes, total_ingresos)
                        VALUES (%s, %s, %s, %s)
                    """, (report_id, year, month, suma))
                    conn.commit()



# --- Scheduler para cerrar masivamente a las 23:59 ---
def close_open_accesses():
    conn = get_db_connection()
    cur  = conn.cursor()
    try:
        cur.execute("""
            UPDATE acceso
               SET hora_salida = time '23:59:59'
             WHERE fecha = CURRENT_DATE
               AND hora_salida IS NULL
        """)
        conn.commit()
    finally:
        cur.close()
        conn.close()

scheduler.add_job(create_monthly_report, 'cron', day=1, hour=0, minute=5)
scheduler.add_job(close_open_accesses, 'cron', hour=23, minute=59)
scheduler.start()

@app.route('/api/status', methods=['GET'])
def parking_status():
    """
    Devuelve para la fecha actual:
      - capacidad_total (porcentaje ocupacion)
      - espacios_disponibles
      - espacios_ocupados
    """
    conn = get_db_connection()
    cur  = conn.cursor()
    try:
        cur.execute("""
            SELECT capacidad_total, espacios_disponibles, espacios_ocupados
              FROM estacionamiento
             WHERE fecha = CURRENT_DATE
        """)
        row = cur.fetchone()
        if not row:
            # Si no hay fila para hoy, asumimos 0% ocupado
            return jsonify({
                'capacidad_total': 0.0,
                'espacios_disponibles': 2000,
                'espacios_ocupados': 0
            })
        cap, disp, occ = row
        return jsonify({
            'capacidad_total': float(cap),
            'espacios_disponibles': disp,
            'espacios_ocupados': occ
        })
    finally:
        cur.close()
        conn.close()

# --- AGENDAR EL JOB PARA PROBAR EN 1 MINUTO ---
def programar_test_job():
    ahora = datetime.now()
    minuto_siguiente = (ahora + timedelta(minutes=1)).minute
    hora_actual = ahora.hour
    scheduler.add_job(
        close_open_accesses, 
        'cron',
        hour=hora_actual,
        minute=minuto_siguiente,
        id='test_close_open_accesses'
    )
    print(f"Job programado para las {hora_actual}:{str(minuto_siguiente).zfill(2)}")

# programar_test_job() 




# Mostrar rutas al iniciar el servidor
print("=== RUTAS REGISTRADAS EN FLASK ===")
for rule in app.url_map.iter_rules():
    print(rule)
print("===================================")



if __name__ == '__main__':
    app.run()
