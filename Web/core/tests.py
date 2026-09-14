import io

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from .validators import TAMANHO_MAXIMO_IMAGEM_MB, validar_imagem


def gerar_imagem_valida(formato="PNG", largura=10, altura=10):
    buffer = io.BytesIO()
    Image.new("RGB", (largura, altura), color="red").save(buffer, format=formato)
    buffer.seek(0)
    return SimpleUploadedFile("foto.png", buffer.read(), content_type="image/png")


class ValidarImagemTests(TestCase):
    def test_imagem_valida_nao_levanta_erro(self):
        arquivo = gerar_imagem_valida()
        validar_imagem(arquivo)  # não deve levantar erro

    def test_arquivo_que_nao_e_imagem_e_rejeitado(self):
        arquivo = SimpleUploadedFile("nota.txt", b"isso nao e uma imagem", content_type="text/plain")
        with self.assertRaises(ValidationError):
            validar_imagem(arquivo)

    def test_imagem_maior_que_o_limite_e_rejeitada(self):
        arquivo = gerar_imagem_valida()
        arquivo.size = (TAMANHO_MAXIMO_IMAGEM_MB * 1024 * 1024) + 1
        with self.assertRaises(ValidationError):
            validar_imagem(arquivo)

    def test_arquivo_com_extensao_de_imagem_mas_conteudo_falso_e_rejeitado(self):
        arquivo = SimpleUploadedFile("foto.png", b"nao sou um png de verdade", content_type="image/png")
        with self.assertRaises(ValidationError):
            validar_imagem(arquivo)
