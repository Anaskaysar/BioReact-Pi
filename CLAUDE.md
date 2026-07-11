BioReact-Pi — Integración del Sensor de Temperatura DS18B20

Resumen completo del proceso de integración del sensor de temperatura para el proyecto BioReact-Pi (CopernicusLAC Hackathon). Incluye lo que se intentó, lo que falló, lo que finalmente funcionó, y el código final listo para integrar con el dashboard.


1. Resumen ejecutivo


Sensor: Dallas/Maxim DS18B20 (protocolo 1-Wire), 3 pines: GND, DATA, VCC.
Conexión final (funcionando): DATA → GPIO17, VCC → 3.3V, GND → GND, todo fijo en breadboard con un cable extensor (no sostenido a mano).
Primer intento (Raspberry Pi 5 con QNX): no funcionó de forma confiable. Se documenta abajo por qué, para no repetir el mismo camino.
Solución final (Raspberry Pi con Ubuntu/Linux): funciona usando el driver nativo del kernel (w1-gpio + w1-therm), sin necesidad de bit-banging manual.
Estado actual: FUNCIONANDO Y VALIDADO. El sensor lee temperatura real vía /sys/bus/w1/devices/28-000000870030/w1_slave, con verificación CRC del propio kernel. Se confirmó reacción real al calor (mano sobre el sensor): subida gradual y consistente de 22.19°C → 28.44°C en pocos segundos, sin saltos erráticos.
Causa raíz del ruido/saltos que se veían antes: conexión física inestable (cables sostenidos a mano, sin breadboard fijo, y posiblemente GPIO4 con mal contacto en ese punto específico). Se resolvió usando un cable extensor a breadboard y cambiando la señal DATA a GPIO17.
Actuadores (heater/fan): aún NO están integrados físicamente (sin LEDs/fans conectados todavía) — el edge server reporta heater_power_pct=0 y fan_speed_pct=0 en vez de simular un valor, para no mostrar datos falsos en el dashboard.
Humedad: no hay DHT22 conectado aún. El dashboard reporta humidity_pct=0 (honesto — no medido), pero internamente el modelo de crecimiento sigue usando 80% asumido para que la curva de biomasa reaccione de forma realista a la temperatura mientras no se integre el sensor real.



2. Intento en QNX (Raspberry Pi 5) — por qué no funcionó

Se intentó primero en un Raspberry Pi 5 corriendo QNX (accedido vía VNC y luego SSH). El plan era implementar el protocolo 1-Wire manualmente ("bit-banging") en Python, usando el módulo rpi_gpio propio de QNX (similar a RPi.GPIO de Linux).

Lo que se descubrió en el proceso:


QNX expone el GPIO a través de un resource manager (rpi_gpio, proceso corriendo en /dev/gpio), que se comunica vía paso de mensajes IPC, no acceso directo a registros de memoria como en Linux.
Cada llamada (GPIO.setup(), GPIO.output(), GPIO.input()) tiene una latencia intrínseca medida empíricamente de ~13-58 microsegundos por llamada.
El protocolo 1-Wire requiere pulsos de 1-15 microsegundos para distinguir un bit "1" de un bit "0", y ventanas de lectura de bit de ~60us máximo.
Como la latencia de una sola llamada IPC ya es comparable o mayor al tiempo total permitido para un bit, es fundamentalmente muy difícil (no imposible en teoría, pero poco confiable en la práctica) lograr bit-banging preciso de 1-Wire en QNX desde Python.


Diagnósticos realizados (útiles como referencia si se vuelve a intentar en QNX):


Confirmar que el proceso rpi_gpio corre: sudo pidin -p rpi_gpio
Medir el overhead real de GPIO.setup()/GPIO.output()/GPIO.input() con time.perf_counter() antes/después de cada llamada.
Usar pull-up interno por software: GPIO.setup(PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP).
Verificar el rise time real de la línea tras soltarla (crítico para saber si el pull-up es suficientemente rápido).
Aislar el problema probando el comando Read ROM (0x33), que debe devolver siempre el mismo valor conocido (0x28, family code del DS18B20) — útil para diferenciar problemas de escritura vs. lectura sin depender de la conversión de temperatura.


Conclusión sobre QNX:

No se descartó por falta de intentos — se probó wiring, pull-ups (interno y verificación de externo), múltiples calibraciones de timing, y aislamiento de escritura vs. lectura. La conclusión práctica, dado el tiempo de hackathon disponible, fue cambiar de sistema operativo en vez de seguir peleando contra una limitación de arquitectura.


3. Solución final — Raspberry Pi con Ubuntu (Linux)

En Ubuntu, el DS18B20 se maneja con los módulos de kernel estándar (w1-gpio, w1-therm), que hacen el bit-banging preciso a nivel de kernel/hardware — no hay que reinventar el protocolo.

3.1 Configuración necesaria

Editar el archivo de arranque:

bashsudo nano /boot/firmware/config.txt

Agregar al final:

dtoverlay=w1-gpio,gpiopin=17,pullup=on

(Nota: se probó primero en GPIO4, con resultados inestables — saltos erráticos de temperatura de hasta ±30°C entre lecturas consecutivas, causados por conexión física floja. Cambiar a GPIO17 junto con fijar el wiring en breadboard con cable extensor resolvió el problema por completo. pullup=on activa el pull-up interno del GPIO.)

Reiniciar:

bashsudo reboot

3.2 Verificar que el sensor se detecta

bashls /sys/bus/w1/devices/

Debe aparecer una carpeta con prefijo 28- (ej. 28-000000870030) — ese es el DS18B20 real. Si solo aparecen carpetas con prefijo 00-, es señal de pull-up insuficiente (revisar wiring/resistencia).

3.3 Leer directamente (sin Python, para pruebas rápidas)

bashcat /sys/bus/w1/devices/28-000000870030/w1_slave

Salida esperada:

a3 01 4b 46 7f ff 0c 10 d8 : crc=d8 YES
a3 01 4b 46 7f ff 0c 10 d8 t=26187

YES = CRC válido. t=26187 = 26.187°C (dividir entre 1000).

Nota: si ves t=85000 (85.0°C) de forma persistente, es el valor de reset de fábrica del sensor — normalmente indica problema de alimentación/conexión durante la conversión, no una lectura real.


4. Código final

4.1 test_sensor.py — lectura simple por consola

pythonimport os
import glob
import time

base_dir = '/sys/bus/w1/devices/'
device_folder = glob.glob(base_dir + '28*')[0]
device_file = device_folder + '/w1_slave'


def read_temp_raw():
    with open(device_file, 'r') as f:
        return f.readlines()


def read_temp():
    lines = read_temp_raw()
    while lines[0].strip()[-3:] != 'YES':
        time.sleep(0.2)
        lines = read_temp_raw()
    equals_pos = lines[1].find('t=')
    if equals_pos != -1:
        temp_string = lines[1][equals_pos+2:]
        temp_c = float(temp_string) / 1000.0
        return temp_c


if __name__ == "__main__":
    print(f"Leyendo sensor en: {device_folder}")
    try:
        while True:
            temp = read_temp()
            print(f"Temp: {temp:.2f} °C")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nSaliendo...")

4.2 server.py — API Flask para que el dashboard/UI lo consuma

pythonfrom flask import Flask, jsonify
import time

# Ruta del sensor DS18B20 (ajustar el ID si cambia el sensor/dispositivo)
device_file = '/sys/bus/w1/devices/28-000000870030/w1_slave'

app = Flask(__name__)


def read_temp_raw():
    with open(device_file, 'r') as f:
        return f.readlines()


def read_temp():
    lines = read_temp_raw()
    while lines[0].strip()[-3:] != 'YES':
        time.sleep(0.2)
        lines = read_temp_raw()
    equals_pos = lines[1].find('t=')
    if equals_pos != -1:
        temp_string = lines[1][equals_pos+2:]
        temp_c = float(temp_string) / 1000.0
        return temp_c


@app.route("/data")
def get_data():
    return jsonify({
        "temperature": read_temp(),
        "unit": "Celsius"
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

Para integrar con el dashboard/UI de tu equipo: este servidor expone GET http://<IP_DEL_PI>:5000/data, devolviendo:

json{"temperature": 23.5, "unit": "Celsius"}

El frontend (React + Leaflet.js según la arquitectura de YVY, o el dashboard de BioReact-Pi) puede hacer fetch() a ese endpoint cada 1-2 segundos para actualizar el valor en vivo.


5. Cómo conectarse al Raspberry Pi (referencia rápida)

Conexión directa por cable Ethernet (sin router):


En Windows, activar Internet Connection Sharing (ICS) en el adaptador WiFi, compartiendo hacia el adaptador Ethernet físico correcto (cuidado: en tu laptop había un adaptador virtual de VirtualBox también llamado "Ethernet 5" — el correcto es el que dice "Realtek PCIe GbE Family Controller").
Alternativa más simple y confiable: asignar IP fija manualmente en el Pi:


bash   sudo nmcli connection modify "Wired connection 1" ipv4.addresses 169.254.243.2/16 ipv4.method manual
   sudo nmcli connection up "Wired connection 1"


Desde Windows, conectar con PuTTY (Host: la IP del Pi, Puerto 22, SSH) o ssh usuario@IP desde PowerShell.



6. Pendientes / siguientes pasos sugeridos


 Fijar el wiring en breadboard — RESUELTO: usando cable extensor + breadboard + cambio a GPIO17, las lecturas ahora son estables y reaccionan correctamente al calor (validado poniendo la mano sobre el sensor: subida gradual 22.19°C → 28.44°C).
 Integrar server.py con el dashboard del equipo — RESUELTO: edge/pi_edge_server.py expone /api/telemetry en el formato que consume ui/api/hardware.py; el dashboard corre con BIOREACTOR_DATA_SOURCE=hardware apuntando a la IP del Pi.
 Considerar agregar promedio de varias lecturas (ej. últimas 3-5) en el backend para suavizar ruido residual antes de mandar el dato al frontend — RESUELTO: edge/pi_edge_server.py usa la mediana de las últimas 5 lecturas.
 Documentar el ID del sensor (28-000000870030) en caso de reemplazarlo — el ID cambia por sensor físico.
 Actualizar server.py y cualquier otro script que dependa del pin si se vuelve a cambiar de GPIO (recordar: el pin se configura en /boot/firmware/config.txt, no en el script Python — el script Python solo usa el ID 28-..., que no cambia).
 Integrar actuadores físicos (heater/fan) — pendiente. Hasta entonces, heater_power_pct y fan_speed_pct se reportan en 0 (no simulados) para no mostrar datos falsos.
 Integrar sensor de humedad (DHT22) — pendiente. Hasta entonces, humidity_pct se reporta en 0 en el dashboard; el modelo de crecimiento internamente asume 80% para que la curva de biomasa siga siendo representativa.