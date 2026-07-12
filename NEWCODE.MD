import lgpio
import time


GPIO = 18


h = lgpio.gpiochip_open(0)


def read_dht11():

    # mandar señal inicial
    lgpio.gpio_claim_output(h, GPIO)

    lgpio.gpio_write(h, GPIO, 0)
    time.sleep(0.02)

    lgpio.gpio_write(h, GPIO, 1)
    time.sleep(0.00003)

    # cambiar a entrada
    lgpio.gpio_claim_input(h, GPIO)


    # leer cambios
    valores = []

    inicio = time.time()

    ultimo = lgpio.gpio_read(h, GPIO)


    while time.time() - inicio < 0.1:

        actual = lgpio.gpio_read(h, GPIO)

        if actual != ultimo:
            valores.append(
                (
                    actual,
                    time.time()
                )
            )
            ultimo = actual


    if len(valores) < 80:
        return None, f"pocos pulsos: {len(valores)}"


    bits = []


    # extraer pulsos HIGH
    for i in range(len(valores)-1):

        estado, t1 = valores[i]
        siguiente, t2 = valores[i+1]


        if estado == 1:

            duracion = (t2-t1)*1000000

            if duracion > 40:
                bits.append(1)
            else:
                bits.append(0)


    if len(bits) < 40:
        return None, f"solo {len(bits)} bits"


    bits = bits[:40]


    datos=[]

    for i in range(5):

        byte=0

        for b in bits[i*8:(i+1)*8]:
            byte=(byte<<1)|b

        datos.append(byte)


    humedad=datos[0]
    temperatura=datos[2]
    checksum=datos[4]


    calculado=sum(datos[:4]) & 0xff


    if checksum != calculado:
        return None, f"CRC {datos}"


    return {
        "temp": temperatura,
        "hum": humedad
    }, "OK"



try:

    print("DHT11 Pi 5 GPIO18")
    print("Leyendo...\n")


    while True:

        resultado, estado = read_dht11()


        if resultado:

            print(
                f"Temperatura: {resultado['temp']} C "
                f"Humedad: {resultado['hum']}%"
            )

        else:
            print("Error:", estado)


        time.sleep(2)



except KeyboardInterrupt:
    pass


finally:
    lgpio.gpiochip_close(h)




    so this is for the DH18, please integrate it, it is connected already, just show the humidity, i dont need the temperature, also integrate it into que whole system. 