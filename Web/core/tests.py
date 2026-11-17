import io

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from PIL import Image

from .checks import verificar_num_proxies
from .validators import (
    TAMANHO_MAXIMO_IMAGEM_MB,
    somente_digitos,
    validar_cnpj,
    validar_cpf,
    validar_imagem,
)


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


class ValidarCpfCnpjTests(TestCase):
    def test_cpf_valido_e_aceito(self):
        self.assertTrue(validar_cpf("176.626.330-55"))

    def test_cpf_com_digito_verificador_errado_e_recusado(self):
        self.assertFalse(validar_cpf("176.626.330-00"))

    def test_cpf_com_todos_os_digitos_iguais_e_recusado(self):
        self.assertFalse(validar_cpf("111.111.111-11"))

    def test_cpf_com_tamanho_errado_e_recusado(self):
        self.assertFalse(validar_cpf("123"))

    def test_cnpj_valido_e_aceito(self):
        self.assertTrue(validar_cnpj("74.286.058/0001-80"))

    def test_cnpj_com_digito_verificador_errado_e_recusado(self):
        self.assertFalse(validar_cnpj("74.286.058/0001-00"))

    def test_cnpj_com_todos_os_digitos_iguais_e_recusado(self):
        self.assertFalse(validar_cnpj("11.111.111/1111-11"))

    def test_somente_digitos_remove_pontuacao(self):
        self.assertEqual(somente_digitos("176.626.330-55"), "17662633055")


class VerificarNumProxiesTests(SimpleTestCase):
    """WR-08: `check --deploy` avisa quando a produção ficou com o NUM_PROXIES padrão."""

    @override_settings(DEBUG=False, NUM_PROXIES_CONFIGURADO=False)
    def test_producao_sem_num_proxies_informado_avisa(self):
        avisos = verificar_num_proxies(None)
        self.assertEqual([aviso.id for aviso in avisos], ["core.W001"])

    @override_settings(DEBUG=False, NUM_PROXIES_CONFIGURADO=True)
    def test_producao_com_num_proxies_informado_nao_avisa(self):
        self.assertEqual(verificar_num_proxies(None), [])

    @override_settings(DEBUG=True, NUM_PROXIES_CONFIGURADO=False)
    def test_desenvolvimento_nao_avisa(self):
        self.assertEqual(verificar_num_proxies(None), [])

    def test_o_aviso_roda_no_check_deploy(self):
        from django.core.checks import run_checks

        with override_settings(DEBUG=False, NUM_PROXIES_CONFIGURADO=False):
            ids = [aviso.id for aviso in run_checks(include_deployment_checks=True)]
        self.assertIn("core.W001", ids)
        # e fica fora do `check` comum, que roda na partida e na suíte de testes
        with override_settings(DEBUG=False, NUM_PROXIES_CONFIGURADO=False):
            self.assertNotIn("core.W001", [aviso.id for aviso in run_checks()])
