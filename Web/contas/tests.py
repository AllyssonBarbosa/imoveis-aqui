from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from empresas.models import Empresa

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


class LoginApiTests(APITestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.usuario = criar_corretor(self.empresa)

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
