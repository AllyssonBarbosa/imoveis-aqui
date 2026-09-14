from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

TAMANHO_MAXIMO_IMAGEM_MB = 5
FORMATOS_DE_IMAGEM_PERMITIDOS = {"JPEG", "PNG", "WEBP"}


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
