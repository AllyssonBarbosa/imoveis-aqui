import re

from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

TAMANHO_MAXIMO_IMAGEM_MB = 5
FORMATOS_DE_IMAGEM_PERMITIDOS = {"JPEG", "PNG", "WEBP"}


def somente_digitos(valor):
    return re.sub(r"\D", "", valor or "")


def validar_cpf(cpf):
    """Confere o CPF pelo algoritmo dos dígitos verificadores (não só o formato)."""
    cpf = somente_digitos(cpf)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False

    for posicao in (9, 10):
        soma = sum(
            int(cpf[indice]) * (posicao + 1 - indice) for indice in range(posicao)
        )
        digito = (soma * 10 % 11) % 10
        if digito != int(cpf[posicao]):
            return False
    return True


def validar_cnpj(cnpj):
    """Confere o CNPJ pelo algoritmo dos dígitos verificadores (não só o formato)."""
    cnpj = somente_digitos(cnpj)
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        return False

    pesos_primeiro_digito = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos_segundo_digito = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]

    def calcular_digito(base, pesos):
        soma = sum(int(digito) * peso for digito, peso in zip(base, pesos))
        resto = soma % 11
        return "0" if resto < 2 else str(11 - resto)

    primeiro_digito = calcular_digito(cnpj[:12], pesos_primeiro_digito)
    segundo_digito = calcular_digito(cnpj[:12] + primeiro_digito, pesos_segundo_digito)
    return cnpj[-2:] == primeiro_digito + segundo_digito


def validar_imagem(arquivo):
    """Confere tamanho e, abrindo o arquivo de verdade com o Pillow, o formato da imagem.

    Não confia no content-type enviado pelo navegador (é fácil de forjar).
    """
    tamanho_maximo = TAMANHO_MAXIMO_IMAGEM_MB * 1024 * 1024
    if arquivo.size > tamanho_maximo:
        raise ValidationError(
            f"A imagem não pode passar de {TAMANHO_MAXIMO_IMAGEM_MB}MB."
        )

    posicao_original = arquivo.tell()
    arquivo.seek(0)
    try:
        formato = Image.open(arquivo).format
    except UnidentifiedImageError:
        raise ValidationError("O arquivo enviado não é uma imagem válida.")
    finally:
        arquivo.seek(posicao_original)

    if formato not in FORMATOS_DE_IMAGEM_PERMITIDOS:
        raise ValidationError("Formato de imagem não permitido. Use JPG, PNG ou WEBP.")
