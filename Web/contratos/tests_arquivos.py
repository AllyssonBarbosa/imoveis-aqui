"""Testes da W12 — etapa 4: documentos do contrato em pasta privada.

Os arquivos têm dados pessoais: não podem ter URL pública, só usuário logado
da mesma empresa baixa (e o corretor, só dos contratos dele), e tipo e
tamanho são conferidos pelo conteúdo.
"""

import io
import os
import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from .arquivos import armazenamento_privado
from .models import DocumentoContrato
from .servicos import salvar_contrato
from .tests import CenarioContrato

CONTEUDO_PDF = b"%PDF-1.4\n%documento de teste\n"


def gerar_pdf(nome="contrato.pdf"):
    return SimpleUploadedFile(nome, CONTEUDO_PDF, content_type="application/pdf")


def gerar_png(nome="rg.png"):
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10), color="red").save(buffer, format="PNG")
    return SimpleUploadedFile(nome, buffer.getvalue(), content_type="image/png")


class ArquivosBase(CenarioContrato):
    """Pasta privada temporária para cada teste — nada é gravado na pasta de verdade."""

    def setUp(self):
        self.pasta = tempfile.mkdtemp()
        self.configuracao = override_settings(ARQUIVOS_PRIVADOS_ROOT=Path(self.pasta))
        self.configuracao.enable()
        self.addCleanup(self.configuracao.disable)
        self.addCleanup(shutil.rmtree, self.pasta, ignore_errors=True)

        self.montar_cenario()
        self.contrato = salvar_contrato(self.novo_contrato(), self.gestor)
        self.contrato_do_outro_corretor = salvar_contrato(
            self.novo_contrato(corretor=self.corretor2), self.gestor
        )

    def anexar(self, contrato=None, arquivo=None, tipo=DocumentoContrato.Tipo.CONTRATO_ASSINADO):
        contrato = contrato or self.contrato
        documento = DocumentoContrato(
            contrato=contrato, empresa_id=contrato.empresa_id, tipo=tipo,
            arquivo=arquivo or gerar_pdf(), enviado_por=self.gestor,
        )
        documento.full_clean()
        documento.save()
        return documento

    def caminho_no_disco(self, documento):
        return Path(documento.arquivo.path)


class ArmazenamentoPrivadoTests(ArquivosBase, TestCase):
    def test_arquivo_fica_fora_do_media_root(self):
        documento = self.anexar()
        caminho = self.caminho_no_disco(documento).resolve()
        self.assertTrue(caminho.exists())
        self.assertTrue(str(caminho).startswith(str(Path(self.pasta).resolve())))
        self.assertFalse(str(caminho).startswith(str(Path(settings.MEDIA_ROOT).resolve())))

    def test_pasta_padrao_tambem_fica_fora_do_media_root(self):
        self.configuracao.disable()
        try:
            privada = Path(settings.ARQUIVOS_PRIVADOS_ROOT).resolve()
            media = Path(settings.MEDIA_ROOT).resolve()
            self.assertNotEqual(privada, media)
            self.assertNotIn(media, privada.parents)
        finally:
            self.configuracao.enable()

    def test_arquivo_privado_nao_tem_url(self):
        documento = self.anexar()
        with self.assertRaises(ValueError):
            documento.arquivo.url
        with self.assertRaises(ValueError):
            armazenamento_privado.url("qualquer.pdf")

    def test_nome_no_disco_e_aleatorio_e_extensao_vem_do_conteudo(self):
        documento = self.anexar(arquivo=gerar_png(nome="RG Maria Compradora 529.982.247-25.pdf"))
        nome = os.path.basename(documento.arquivo.name)
        self.assertRegex(nome, r"^[0-9a-f]{32}\.png$")
        self.assertNotIn("Maria", documento.arquivo.name)
        self.assertIn(f"contratos/{self.empresa.pk}/documentos/", documento.arquivo.name)

    def test_documento_pertence_a_empresa_do_contrato(self):
        self.assertEqual(self.anexar().empresa_id, self.empresa.pk)


class ValidacaoDoArquivoTests(ArquivosBase, TestCase):
    def erro(self, arquivo):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError) as erro:
            self.anexar(arquivo=arquivo)
        return str(erro.exception)

    def test_aceita_pdf_jpg_e_png(self):
        buffer = io.BytesIO()
        Image.new("RGB", (10, 10)).save(buffer, format="JPEG")
        jpg = SimpleUploadedFile("foto.jpg", buffer.getvalue(), content_type="image/jpeg")
        for arquivo in (gerar_pdf(), gerar_png(), jpg):
            with self.subTest(arquivo=arquivo.name):
                self.anexar(arquivo=arquivo)
        self.assertEqual(DocumentoContrato.objects.count(), 3)

    def test_recusa_executavel_com_nome_de_pdf(self):
        falso = SimpleUploadedFile("contrato.pdf", b"MZ\x90\x00 programa", content_type="application/pdf")
        self.assertIn("Use PDF, JPG ou PNG", self.erro(falso))

    def test_recusa_formato_de_imagem_nao_permitido(self):
        buffer = io.BytesIO()
        Image.new("RGB", (10, 10)).save(buffer, format="GIF")
        gif = SimpleUploadedFile("rg.gif", buffer.getvalue(), content_type="image/gif")
        self.assertIn("Use PDF, JPG ou PNG", self.erro(gif))

    def test_recusa_arquivo_maior_que_10mb(self):
        grande = SimpleUploadedFile(
            "grande.pdf", b"%PDF-" + b"0" * (10 * 1024 * 1024), content_type="application/pdf"
        )
        self.assertIn("10MB", self.erro(grande))
        self.assertEqual(os.listdir(self.pasta), [])


class PainelDocumentosTests(ArquivosBase, TestCase):
    def enviar(self, usuario, contrato=None, arquivo=None):
        self.client.force_login(usuario)
        contrato = contrato or self.contrato
        return self.client.post(
            reverse("contratos:documento_enviar", args=[contrato.pk]),
            {"tipo": "DOCUMENTO_CLIENTE", "descricao": "RG", "arquivo": arquivo or gerar_pdf()},
        )

    def baixar(self, usuario, documento):
        if usuario:
            self.client.force_login(usuario)
        return self.client.get(
            reverse("contratos:documento_baixar", args=[documento.contrato_id, documento.pk])
        )

    def test_gestor_anexa_documento(self):
        response = self.enviar(self.gestor)
        self.assertRedirects(response, reverse("contratos:contrato_detail", args=[self.contrato.pk]))
        documento = DocumentoContrato.objects.get()
        self.assertEqual(documento.enviado_por, self.gestor)
        self.assertEqual(documento.contrato, self.contrato)

    def test_arquivo_invalido_mostra_mensagem_e_nao_grava(self):
        falso = SimpleUploadedFile("x.pdf", b"nada", content_type="application/pdf")
        response = self.enviar(self.gestor, arquivo=falso)
        detalhe = self.client.get(response["Location"])
        self.assertContains(detalhe, "Não deu pra anexar o documento")
        self.assertFalse(DocumentoContrato.objects.exists())

    def test_corretor_e_administrador_nao_anexam(self):
        for usuario in (self.corretor, self.admin):
            with self.subTest(perfil=usuario.perfil):
                self.assertEqual(self.enviar(usuario).status_code, 403)
        self.assertFalse(DocumentoContrato.objects.exists())

    def test_gestor_de_outra_empresa_nao_anexa(self):
        self.assertEqual(self.enviar(self.gestor_b).status_code, 404)

    def test_gestor_baixa_como_anexo_sem_cache_e_sem_adivinhar_tipo(self):
        documento = self.anexar()
        response = self.baixar(self.gestor, documento)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), CONTEUDO_PDF)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn(f"contrato-{self.contrato.pk}-contrato_assinado-{documento.pk}.pdf",
                      response["Content-Disposition"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("no-store", response["Cache-Control"])

    def test_corretor_do_contrato_baixa(self):
        self.assertEqual(self.baixar(self.corretor, self.anexar()).status_code, 200)

    def test_corretor_de_outro_contrato_nao_baixa(self):
        documento = self.anexar(contrato=self.contrato_do_outro_corretor)
        self.assertEqual(self.baixar(self.corretor, documento).status_code, 404)

    def test_outra_empresa_nao_baixa(self):
        self.assertEqual(self.baixar(self.gestor_b, self.anexar()).status_code, 404)

    def test_administrador_nao_baixa(self):
        self.assertEqual(self.baixar(self.admin, self.anexar()).status_code, 403)

    def test_anonimo_vai_para_o_login(self):
        response = self.baixar(None, self.anexar())
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("contas:login"), response["Location"])

    def test_documento_de_outro_contrato_pela_url_do_meu_contrato_da_404(self):
        """Trocar só o id do documento na URL não abre documento de outro contrato."""
        alheio = self.anexar(contrato=self.contrato_do_outro_corretor)
        self.client.force_login(self.corretor)
        response = self.client.get(
            reverse("contratos:documento_baixar", args=[self.contrato.pk, alheio.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_tela_lista_documento_sem_endereco_do_arquivo(self):
        documento = self.anexar()
        self.client.force_login(self.gestor)
        response = self.client.get(reverse("contratos:contrato_detail", args=[self.contrato.pk]))
        self.assertContains(
            response, reverse("contratos:documento_baixar", args=[self.contrato.pk, documento.pk])
        )
        self.assertNotContains(response, documento.arquivo.name)
        self.assertNotContains(response, "/media/")

    def test_gestor_remove_documento_e_o_arquivo_some_do_disco(self):
        documento = self.anexar()
        caminho = self.caminho_no_disco(documento)
        self.client.force_login(self.gestor)
        self.client.post(reverse("contratos:documento_excluir", args=[self.contrato.pk, documento.pk]))
        self.assertFalse(DocumentoContrato.objects.exists())
        self.assertFalse(caminho.exists())

    def test_corretor_nao_remove(self):
        documento = self.anexar()
        self.client.force_login(self.corretor)
        response = self.client.post(
            reverse("contratos:documento_excluir", args=[self.contrato.pk, documento.pk])
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(self.caminho_no_disco(documento).exists())


class ApiDocumentosTests(ArquivosBase, APITestCase):
    def enviar(self, usuario, contrato=None, arquivo=None):
        self.client.force_authenticate(usuario)
        contrato = contrato or self.contrato
        return self.client.post(
            reverse("documento-contrato-list"),
            {"contrato": contrato.pk, "tipo": "CONTRATO_ASSINADO", "arquivo": arquivo or gerar_pdf()},
            format="multipart",
        )

    def baixar(self, usuario, documento):
        if usuario:
            self.client.force_authenticate(usuario)
        return self.client.get(reverse("documento-contrato-baixar", args=[documento.pk]))

    def test_gestor_envia_e_resposta_nao_tem_endereco_do_arquivo(self):
        response = self.enviar(self.gestor)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertNotIn("arquivo", response.data)
        documento = DocumentoContrato.objects.get()
        self.assertNotIn(documento.arquivo.name, str(response.data))
        self.assertTrue(response.data["url_download"].endswith(
            reverse("documento-contrato-baixar", args=[documento.pk])
        ))

    def test_contrato_traz_a_lista_de_documentos(self):
        documento = self.anexar()
        self.client.force_authenticate(self.gestor)
        response = self.client.get(reverse("contrato-detail", args=[self.contrato.pk]))
        self.assertEqual([d["id"] for d in response.data["documentos"]], [documento.pk])
        self.assertNotIn(documento.arquivo.name, str(response.data))

    def test_arquivo_invalido_e_recusado(self):
        falso = SimpleUploadedFile("x.pdf", b"nada", content_type="application/pdf")
        response = self.enviar(self.gestor, arquivo=falso)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("arquivo", response.data)
        self.assertEqual(os.listdir(self.pasta), [])

    def test_corretor_nao_envia(self):
        self.assertEqual(self.enviar(self.corretor).status_code, status.HTTP_403_FORBIDDEN)

    def test_nao_envia_para_contrato_de_outra_empresa(self):
        response = self.enviar(self.gestor_b, contrato=self.contrato)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("contrato", response.data)
        self.assertFalse(DocumentoContrato.objects.exists())

    def test_gestor_e_corretor_do_contrato_baixam(self):
        documento = self.anexar()
        for usuario in (self.gestor, self.corretor):
            with self.subTest(perfil=usuario.perfil):
                response = self.baixar(usuario, documento)
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(b"".join(response.streaming_content), CONTEUDO_PDF)

    def test_quem_nao_pode_ver_nao_baixa(self):
        documento = self.anexar(contrato=self.contrato_do_outro_corretor)
        casos = [
            (self.corretor, status.HTTP_404_NOT_FOUND),  # contrato de outro corretor
            (self.gestor_b, status.HTTP_404_NOT_FOUND),  # outra empresa
            (self.admin, status.HTTP_403_FORBIDDEN),  # sem empresa
        ]
        for usuario, esperado in casos:
            with self.subTest(usuario=usuario.email):
                self.assertEqual(self.baixar(usuario, documento).status_code, esperado)

    def test_anonimo_recebe_401(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.baixar(None, self.anexar()).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lista_so_documentos_que_o_usuario_pode_ver(self):
        meu = self.anexar()
        self.anexar(contrato=self.contrato_do_outro_corretor)
        self.client.force_authenticate(self.corretor)
        response = self.client.get(reverse("documento-contrato-list"))
        self.assertEqual([d["id"] for d in response.data], [meu.pk])
        self.client.force_authenticate(self.gestor_b)
        self.assertEqual(self.client.get(reverse("documento-contrato-list")).data, [])

    def test_gestor_exclui_e_arquivo_some_do_disco(self):
        documento = self.anexar()
        caminho = self.caminho_no_disco(documento)
        self.client.force_authenticate(self.gestor)
        response = self.client.delete(reverse("documento-contrato-detail", args=[documento.pk]))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(caminho.exists())

    def test_corretor_nao_exclui(self):
        documento = self.anexar()
        self.client.force_authenticate(self.corretor)
        response = self.client.delete(reverse("documento-contrato-detail", args=[documento.pk]))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(DocumentoContrato.objects.exists())
