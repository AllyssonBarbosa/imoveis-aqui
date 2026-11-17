"""Arquivos privados da W12: documentos do contrato e comprovantes de pagamento.

Têm dados pessoais (CPF, renda, conta bancária), então:

- ficam numa pasta FORA do MEDIA_ROOT (settings.ARQUIVOS_PRIVADOS_ROOT), que o
  Django nunca serve por URL — nem com DEBUG ligado;
- o armazenamento não tem URL: chamar .url() dá erro, de propósito, para que
  ninguém coloque o endereço do arquivo num template ou numa resposta da API;
- o nome no disco é aleatório (uuid) e a extensão vem do conteúdo, não do nome enviado;
- o download só acontece por uma view que confere login, empresa e perfil,
  e sai como anexo (o navegador baixa em vez de abrir na página).

A validação de tipo e tamanho reaproveita a da W08 (core.validators.validar_documento):
PDF, JPG ou PNG de até 10MB, conferido pelo conteúdo do arquivo.
"""

import os
import uuid

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.http import FileResponse
from django.utils.deconstruct import deconstructible
from PIL import Image, UnidentifiedImageError

TIPOS_DE_CONTEUDO = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".png": "image/png"}


@deconstructible
class ArmazenamentoPrivado(FileSystemStorage):
    """FileSystemStorage apontando para ARQUIVOS_PRIVADOS_ROOT e sem URL pública.

    A pasta é lida da configuração na hora do uso (e não na importação), para
    que os testes possam trocá-la por uma pasta temporária.
    """

    def __init__(self):
        super().__init__(base_url=None)

    @property
    def base_location(self):
        return settings.ARQUIVOS_PRIVADOS_ROOT

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    def url(self, name):
        raise ValueError("Arquivo privado não tem URL pública: use a rota de download, que confere a empresa.")


armazenamento_privado = ArmazenamentoPrivado()


def extensao_pelo_conteudo(arquivo):
    """'.pdf', '.jpg' ou '.png' conforme o conteúdo (o nome enviado pode mentir)."""
    posicao = arquivo.tell()
    arquivo.seek(0)
    try:
        if arquivo.read(5) == b"%PDF-":
            return ".pdf"
        arquivo.seek(0)
        try:
            formato = Image.open(arquivo).format
        except UnidentifiedImageError:
            formato = None
    finally:
        arquivo.seek(posicao)
    return {"JPEG": ".jpg", "PNG": ".png"}.get(formato, ".bin")


def _caminho(instancia, pasta):
    """contratos/<empresa>/<pasta>/<uuid>.<ext> — nada no nome identifica o cliente."""
    extensao = extensao_pelo_conteudo(instancia.arquivo.file)
    return f"contratos/{instancia.empresa_id}/{pasta}/{uuid.uuid4().hex}{extensao}"


def caminho_documento(instancia, nome_original):
    return _caminho(instancia, "documentos")


def caminho_comprovante(instancia, nome_original):
    return _caminho(instancia, "comprovantes")


def responder_download(campo_arquivo, nome_para_download):
    """Entrega o arquivo como anexo, sem cache compartilhado e sem o navegador adivinhar o tipo."""
    extensao = os.path.splitext(campo_arquivo.name)[1].lower()
    resposta = FileResponse(
        campo_arquivo.open("rb"),
        as_attachment=True,
        filename=f"{nome_para_download}{extensao}",
        content_type=TIPOS_DE_CONTEUDO.get(extensao, "application/octet-stream"),
    )
    resposta["X-Content-Type-Options"] = "nosniff"
    resposta["Cache-Control"] = "private, no-store"
    return resposta
