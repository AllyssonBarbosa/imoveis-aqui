from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, APITestCase

from core.throttles import ThrottleLoginPorEmail, ThrottleLoginPorIp
from empresas.models import Empresa

from .api.views import LoginAPIView
from .models import Usuario


def criar_empresa(nome="Imobiliária Teste", cnpj="11.111.111/0001-11", tipo=None, cpf=None):
    return Empresa.objects.create(
        tipo=tipo or Empresa.Tipo.IMOBILIARIA,
        razao_social_ou_nome=nome,
        cnpj=None if tipo == Empresa.Tipo.AUTONOMO else cnpj,
        cpf=cpf,
        creci="12345-J",
        descricao="Apresentação da empresa.",
        whatsapp="11999999999",
        telefone="1133334444",
        email="contato@teste.com",
    )


def criar_corretor(empresa, email="corretor@teste.com", nome="Corretor", **extra):
    extra.setdefault("creci", "54321-F")
    extra.setdefault("telefone", "11988887777")
    extra.setdefault("whatsapp", "11988887777")
    return Usuario.objects.create_user(
        email=email,
        senha="senha-forte-123",
        nome=nome,
        perfil=Usuario.Perfil.CORRETOR,
        empresa=empresa,
        **extra,
    )


class UsuarioModelTests(TestCase):
    def test_administrador_nao_pode_ter_empresa(self):
        empresa = criar_empresa()
        usuario = Usuario(
            nome="Admin",
            email="admin2@teste.com",
            perfil=Usuario.Perfil.ADMINISTRADOR,
            empresa=empresa,
        )
        usuario.set_password("senha-forte-123")
        with self.assertRaises(ValidationError):
            usuario.full_clean()

    def test_gestor_precisa_de_empresa(self):
        usuario = Usuario(
            nome="Gestor",
            email="gestor@teste.com",
            perfil=Usuario.Perfil.GESTOR,
        )
        usuario.set_password("senha-forte-123")
        with self.assertRaises(ValidationError):
            usuario.full_clean()

    def test_corretor_de_imobiliaria_sem_creci_e_invalido(self):
        empresa = criar_empresa()
        usuario = Usuario(
            nome="Corretor",
            email="corretor@teste.com",
            perfil=Usuario.Perfil.CORRETOR,
            empresa=empresa,
        )
        usuario.set_password("senha-forte-123")
        with self.assertRaises(ValidationError):
            usuario.full_clean()

    def test_corretor_de_imobiliaria_com_dados_proprios_e_valido(self):
        empresa = criar_empresa()
        usuario = Usuario(
            nome="Corretor",
            email="corretor@teste.com",
            perfil=Usuario.Perfil.CORRETOR,
            empresa=empresa,
            creci="54321-F",
            telefone="11988887777",
            whatsapp="11988887777",
        )
        usuario.set_password("senha-forte-123")
        usuario.full_clean()  # não deve levantar erro

    def test_corretor_autonomo_pode_deixar_dados_em_branco(self):
        empresa = criar_empresa(tipo=Empresa.Tipo.AUTONOMO, cpf="111.111.111-11")
        usuario = Usuario(
            nome="Corretor Autônomo",
            email="autonomo@teste.com",
            perfil=Usuario.Perfil.CORRETOR,
            empresa=empresa,
        )
        usuario.set_password("senha-forte-123")
        usuario.full_clean()  # não deve levantar erro: herda tudo da empresa

    def test_perfil_publico_do_autonomo_herda_da_empresa(self):
        empresa = criar_empresa(tipo=Empresa.Tipo.AUTONOMO, cpf="111.111.111-11")
        usuario = criar_corretor(
            empresa, email="autonomo@teste.com", nome="Corretor Autônomo",
            creci="", telefone="", whatsapp="",
        )
        dados = usuario.dados_perfil_publico()
        self.assertEqual(dados["creci"], empresa.creci)
        self.assertEqual(dados["telefone"], empresa.telefone)
        self.assertEqual(dados["whatsapp"], empresa.whatsapp)
        self.assertEqual(dados["apresentacao"], empresa.descricao)


class LoginWebTests(TestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.usuario = criar_corretor(self.empresa)

    def test_login_com_email_inexistente_da_erro_generico(self):
        response = self.client.post(
            reverse("contas:login"),
            {"username": "naoexiste@teste.com", "password": "qualquer"},
        )
        self.assertContains(response, "entre com um email")
        self.assertNotContains(response, "não existe")

    def test_login_com_senha_errada_mesma_mensagem_de_email_inexistente(self):
        resposta_email_errado = self.client.post(
            reverse("contas:login"),
            {"username": "naoexiste@teste.com", "password": "qualquer"},
        )
        resposta_senha_errada = self.client.post(
            reverse("contas:login"),
            {"username": "corretor@teste.com", "password": "senha-errada"},
        )
        erros_email_errado = resposta_email_errado.context["form"].errors
        erros_senha_errada = resposta_senha_errada.context["form"].errors
        self.assertEqual(erros_email_errado, erros_senha_errada)

    def test_login_com_credenciais_corretas_funciona(self):
        response = self.client.post(
            reverse("contas:login"),
            {"username": "corretor@teste.com", "password": "senha-forte-123"},
        )
        self.assertRedirects(response, reverse("contas:painel"))


# Taxa fixa da suíte: a real vem de LOGIN_TAXA_POR_EMAIL/LOGIN_TAXA_POR_IP no `.env` de quem roda, e um
# valor baixo ali (é para isso que as variáveis existem) faria testes que fazem login receberem 429.
TAXA_ALTA_NOS_TESTES = "100000/min"


def fixar_taxa_alta_no_login(teste):
    """Limpa o cache dos limites e pina uma taxa alta nos dois throttles do login durante o teste.

    Os testes de limite sobrescrevem com a taxa explícita deles (`mock.patch.object` aninhado). A leitura
    do settings segue testável por `get_rate()`, que ignora este patch.
    """
    cache.clear()
    teste.addCleanup(cache.clear)
    for classe in (ThrottleLoginPorEmail, ThrottleLoginPorIp):
        patcher = mock.patch.object(classe, "rate", TAXA_ALTA_NOS_TESTES, create=True)
        patcher.start()
        teste.addCleanup(patcher.stop)


class LoginApiTests(APITestCase):
    def setUp(self):
        fixar_taxa_alta_no_login(self)
        self.empresa = criar_empresa()
        self.usuario = criar_corretor(self.empresa)

    def test_resposta_do_login_tem_so_a_chave_token(self):
        response = self.client.post(
            reverse("api-login"),
            {"email": "corretor@teste.com", "password": "senha-forte-123"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(set(response.data), {"token"})

    def test_token_velho_no_cabecalho_nao_derruba_o_login_com_senha_certa(self):
        response = self.client.post(
            reverse("api-login"),
            {"email": "corretor@teste.com", "password": "senha-forte-123"},
            HTTP_AUTHORIZATION="Token chave-que-nao-existe",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_token_velho_no_cabecalho_com_senha_errada_da_400_e_nunca_401(self):
        response = self.client.post(
            reverse("api-login"),
            {"email": "corretor@teste.com", "password": "errada"},
            HTTP_AUTHORIZATION="Token chave-que-nao-existe",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_retorna_token_valido(self):
        response = self.client.post(
            reverse("api-login"),
            {"email": "corretor@teste.com", "password": "senha-forte-123"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        token = Token.objects.get(user=self.usuario)
        self.assertEqual(response.data["token"], token.key)

    def test_login_com_senha_errada_nao_autentica(self):
        response = self.client.post(
            reverse("api-login"),
            {"email": "corretor@teste.com", "password": "errada"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class LoginLimiteDeTentativasTests(APITestCase):
    """D-A: o login limita as tentativas por e-mail e por IP, sem revelar se o e-mail existe."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.empresa = criar_empresa()
        self.usuario = criar_corretor(self.empresa)
        self.url = reverse("api-login")

    def _tentar(self, email, senha="errada", **extra):
        return self.client.post(self.url, {"email": email, "password": senha}, format="json", **extra)

    def _com_taxas(self, por_email, por_ip):
        patcher_email = mock.patch.object(ThrottleLoginPorEmail, "rate", por_email, create=True)
        patcher_ip = mock.patch.object(ThrottleLoginPorIp, "rate", por_ip, create=True)
        patcher_email.start()
        patcher_ip.start()
        self.addCleanup(patcher_email.stop)
        self.addCleanup(patcher_ip.stop)

    def _requisicao_drf(self, corpo):
        requisicao = APIRequestFactory().post(self.url, corpo, format="json")
        return Request(requisicao, parsers=[JSONParser()])

    def _chave_de_cache_do_email(self, corpo):
        return ThrottleLoginPorEmail().get_cache_key(self._requisicao_drf(corpo), None)

    def test_terceira_tentativa_com_o_mesmo_email_da_429_com_retry_after(self):
        self._com_taxas("2/min", "100000/min")
        self.assertEqual(self._tentar("corretor@teste.com").status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._tentar("corretor@teste.com").status_code, status.HTTP_400_BAD_REQUEST)
        resposta = self._tentar("corretor@teste.com")
        self.assertEqual(resposta.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertIn("Retry-After", resposta)
        self.assertGreater(int(resposta["Retry-After"]), 0)
        self.assertIn("detail", resposta.data)

    def test_senha_certa_tambem_conta_e_e_bloqueada_depois_do_limite(self):
        self._com_taxas("2/min", "100000/min")
        self._tentar("corretor@teste.com")
        self._tentar("corretor@teste.com")
        resposta = self._tentar("corretor@teste.com", senha="senha-forte-123")
        self.assertEqual(resposta.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_tres_emails_diferentes_do_mesmo_ip_dao_429_na_terceira(self):
        self._com_taxas("100000/min", "2/min")
        self.assertEqual(self._tentar("a@teste.com").status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._tentar("b@teste.com").status_code, status.HTTP_400_BAD_REQUEST)
        resposta = self._tentar("c@teste.com")
        self.assertEqual(resposta.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertIn("Retry-After", resposta)

    def test_429_e_igual_para_email_existente_e_inexistente(self):
        self._com_taxas("1/min", "100000/min")
        self._tentar("corretor@teste.com")
        self._tentar("fantasma@teste.com")
        existente = self._tentar("corretor@teste.com")
        inexistente = self._tentar("fantasma@teste.com")
        self.assertEqual(existente.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(inexistente.status_code, existente.status_code)
        self.assertEqual(set(inexistente.data), set(existente.data))

    def test_email_com_caixa_e_espaco_cai_no_mesmo_balde(self):
        self._com_taxas("2/min", "100000/min")
        self._tentar("Corretor@Teste.com ")
        self._tentar("corretor@teste.com")
        resposta = self._tentar("CORRETOR@teste.com")
        self.assertEqual(resposta.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_chave_de_cache_nao_tem_o_email_em_texto_e_normaliza(self):
        chave = self._chave_de_cache_do_email({"email": "Corretor@Teste.com "})
        self.assertIsNotNone(chave)
        self.assertNotIn("corretor", chave.lower())
        self.assertNotIn("teste.com", chave.lower())
        self.assertEqual(chave, self._chave_de_cache_do_email({"email": "corretor@teste.com"}))
        self.assertNotEqual(chave, self._chave_de_cache_do_email({"email": "outro@teste.com"}))

    def test_sem_email_no_corpo_a_chave_e_none_e_o_serializer_responde_400(self):
        self.assertIsNone(self._chave_de_cache_do_email({"password": "x"}))
        self.assertIsNone(self._chave_de_cache_do_email({"email": "   "}))
        resposta = self.client.post(self.url, {"password": "x"}, format="json")
        self.assertEqual(resposta.status_code, status.HTTP_400_BAD_REQUEST)

    def test_corpo_que_nao_e_objeto_nao_derruba_a_chave(self):
        self.assertIsNone(ThrottleLoginPorEmail().get_cache_key(self._requisicao_drf(["a@teste.com"]), None))

    def test_taxas_vem_do_settings(self):
        taxas = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        self.assertEqual(ThrottleLoginPorEmail().get_rate(), taxas["login_email"])
        self.assertEqual(ThrottleLoginPorIp().get_rate(), taxas["login_ip"])
        self.assertEqual(settings.LOGIN_TAXA_POR_EMAIL, taxas["login_email"])
        self.assertEqual(settings.LOGIN_TAXA_POR_IP, taxas["login_ip"])

    def test_view_do_login_usa_os_dois_throttles_e_nenhuma_autenticacao(self):
        self.assertEqual(LoginAPIView.throttle_classes, [ThrottleLoginPorEmail, ThrottleLoginPorIp])
        self.assertEqual(LoginAPIView.authentication_classes, [])


class IsolamentoMultitenantTests(APITestCase):
    """Critério 6 da W01: consulta filtrada por empresa; URL não abre dado de outra."""

    def setUp(self):
        self.empresa_a = criar_empresa(nome="Empresa A", cnpj="11.111.111/0001-11")
        self.empresa_b = criar_empresa(nome="Empresa B", cnpj="22.222.222/0001-22")

        self.corretor_a = criar_corretor(self.empresa_a, email="corretora@teste.com", nome="Corretor A")
        self.corretor_b = criar_corretor(self.empresa_b, email="corretorb@teste.com", nome="Corretor B")

    def test_lista_apenas_usuarios_da_propria_empresa(self):
        self.client.force_authenticate(self.corretor_a)
        response = self.client.get(reverse("usuario-list"))
        emails = [item["email"] for item in response.data]
        self.assertIn("corretora@teste.com", emails)
        self.assertNotIn("corretorb@teste.com", emails)

    def test_trocar_id_na_url_nao_abre_dado_de_outra_empresa(self):
        self.client.force_authenticate(self.corretor_a)
        response = self.client.get(reverse("usuario-detail", args=[self.corretor_b.pk]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_administrador_sem_empresa_nao_acessa_o_endpoint(self):
        admin = Usuario.objects.create_superuser(
            email="admin@teste.com", senha="senha-forte-123", nome="Admin"
        )
        self.client.force_authenticate(admin)
        response = self.client.get(reverse("usuario-list"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


def criar_gestor(empresa, email="gestor@teste.com", nome="Gestor"):
    return Usuario.objects.create_user(
        email=email, senha="senha-forte-123", nome=nome,
        perfil=Usuario.Perfil.GESTOR, empresa=empresa,
    )


class PainelCorretorTests(TestCase):
    """W02: cadastro de corretor pelo gestor, restrito à própria empresa."""

    def setUp(self):
        self.empresa_a = criar_empresa(nome="Empresa A", cnpj="11.111.111/0001-11")
        self.empresa_b = criar_empresa(nome="Empresa B", cnpj="22.222.222/0001-22")
        self.gestor_a = criar_gestor(self.empresa_a)
        self.corretor_a = criar_corretor(self.empresa_a, email="corretora@teste.com", nome="Corretor A")
        self.corretor_b = criar_corretor(self.empresa_b, email="corretorb@teste.com", nome="Corretor B")

    def test_corretor_nao_pode_acessar_a_lista_do_gestor(self):
        self.client.force_login(self.corretor_a)
        response = self.client.get(reverse("contas:corretor_list"))
        self.assertEqual(response.status_code, 403)

    def test_lista_mostra_apenas_corretores_da_propria_empresa(self):
        self.client.force_login(self.gestor_a)
        response = self.client.get(reverse("contas:corretor_list"))
        nomes = [c.nome for c in response.context["corretores"]]
        self.assertIn("Corretor A", nomes)
        self.assertNotIn("Corretor B", nomes)

    def test_gestor_cadastra_corretor_na_propria_empresa(self):
        self.client.force_login(self.gestor_a)
        response = self.client.post(reverse("contas:corretor_create"), {
            "email": "novo@teste.com",
            "nome": "Corretor Novo",
            "creci": "99999-J",
            "telefone": "11977776666",
            "whatsapp": "11977776666",
            "apresentacao": "Trabalho com imóveis há 10 anos.",
            "password1": "SenhaForte#123",
            "password2": "SenhaForte#123",
        })
        self.assertRedirects(response, reverse("contas:corretor_list"))
        novo = Usuario.objects.get(email="novo@teste.com")
        self.assertEqual(novo.perfil, Usuario.Perfil.CORRETOR)
        self.assertEqual(novo.empresa, self.empresa_a)

    def test_gestor_ativa_e_desativa_corretor_da_propria_empresa(self):
        self.client.force_login(self.gestor_a)
        url = reverse("contas:corretor_toggle_ativo", args=[self.corretor_a.pk])

        self.client.post(url)
        self.corretor_a.refresh_from_db()
        self.assertFalse(self.corretor_a.is_active)

        self.client.post(url)
        self.corretor_a.refresh_from_db()
        self.assertTrue(self.corretor_a.is_active)

    def test_gestor_nao_consegue_desativar_corretor_de_outra_empresa(self):
        self.client.force_login(self.gestor_a)
        url = reverse("contas:corretor_toggle_ativo", args=[self.corretor_b.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)
        self.corretor_b.refresh_from_db()
        self.assertTrue(self.corretor_b.is_active)


class CorretorPublicoApiTests(APITestCase):
    """Critério 5 da W02: perfil público do corretor, sem login."""

    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa, apresentacao="Especialista em apartamentos.")

    def test_perfil_publico_nao_exige_login(self):
        response = self.client.get(reverse("publico-corretor", args=[self.corretor.pk]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["nome"], "Corretor")
        self.assertEqual(response.data["creci"], self.corretor.creci)
        self.assertEqual(response.data["apresentacao"], "Especialista em apartamentos.")

    def test_corretor_desativado_nao_aparece_na_vitrine(self):
        self.corretor.is_active = False
        self.corretor.save(update_fields=["is_active"])
        response = self.client.get(reverse("publico-corretor", args=[self.corretor.pk]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class CorretorPublicoEmpresaInativaTests(APITestCase):
    """07.1 (D-11): corretor de empresa inativa não tem perfil público — como no site (WEB-09)."""

    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)

    def test_corretor_de_empresa_inativa_da_404(self):
        self.empresa.ativa = False
        self.empresa.save(update_fields=["ativa"])
        response = self.client.get(reverse("publico-corretor", args=[self.corretor.pk]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_corretor_de_empresa_ativa_continua_200(self):
        response = self.client.get(reverse("publico-corretor", args=[self.corretor.pk]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class EmpresaPublicaApiTests(APITestCase):
    """Critério 5 da W02: perfil público da empresa, sem login."""

    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor_ativo = criar_corretor(
            self.empresa, email="ativo@teste.com", nome="Corretor Ativo"
        )
        self.corretor_inativo = criar_corretor(
            self.empresa, email="inativo@teste.com", nome="Corretor Inativo"
        )
        self.corretor_inativo.is_active = False
        self.corretor_inativo.save(update_fields=["is_active"])

    def test_perfil_publico_da_empresa_lista_so_corretores_ativos(self):
        response = self.client.get(reverse("publico-empresa", args=[self.empresa.pk]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        nomes = [c["nome"] for c in response.data["corretores_ativos"]]
        self.assertIn("Corretor Ativo", nomes)
        self.assertNotIn("Corretor Inativo", nomes)

    def test_empresa_inativa_nao_aparece(self):
        self.empresa.ativa = False
        self.empresa.save(update_fields=["ativa"])
        response = self.client.get(reverse("publico-empresa", args=[self.empresa.pk]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
