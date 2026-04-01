def es_primo(n):
    if n < 2:
        return False
    for divisor in range(2, int(n ** 0.5) + 1):
        if n % divisor == 0:
            return False
    return True


# Prueba con varios números
for numero in [0, 1, 2, 17, 20]:
    if es_primo(numero):
        print(f"{numero} es un número primo.")
    else:
        print(f"{numero} no es un número primo.")
