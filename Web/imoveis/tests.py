import io

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.utils import IntegrityError
from django.test import TestCase
from django.urls import reverse
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from contas.models import Usuario
from core.models import Endereco
from empresas.models import Empresa
from localizacao.models import Cidade
from proprietarios.models import Proprietario

from .models import Caracteristica, FotoImovel, Imovel


def criar_empresa(nome="Imobiliária Teste", cnpj="74.286.058/0001-80"):
    return Empresa.objects.create(
        tipo=Empresa.Tipo.IMOBILIARIA, razao_social_ou_nome=nome, cnpj=cnpj,
        creci="12345-J", whatsapp="11999999999", email="contato@teste.com",
    )


def criar_corretor(empresa, email="corretor@teste.com"):
    return Usuario.objects.create_user(
        email=email, senha="senha-forte-123", nome="Corretor Teste",
        perfil=Usuario.Perfil.CORRETOR, empresa=empresa,
        creci="54321-F", telefone="11988887777", whatsapp="11988887777",
    )


def criar_gestor(empresa, email="gestor@teste.com"):
    return Usuario.objects.create_user(
        email=email, senha="senha-forte-123", nome="Gestor Teste",
        perfil=Usuario.Perfil.GESTOR, empresa=empresa,
    )


def criar_proprietario(empresa, documento="17662633055"):
    return Proprietario.objects.create(
        empresa=empresa, tipo_pessoa=Proprietario.TipoPessoa.FISICA,
        nome_razao_social="Proprietário Teste", documento=documento,
    )


def criar_imovel(empresa, corretor, proprietario, codigo="IM001", **extra):
    dados = dict(
        empresa=empresa, codigo=codigo, titulo="Casa bonita",
        corretor_responsavel=corretor, proprietario=proprietario,
        finalidade=Imovel.Finalidade.VENDA, preco_venda="300000.00",
    )
    dados.update(extra)
    return Imovel.objects.create(**dados)


def gerar_imagem():
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10), color="blue").save(buffer, format="PNG")
    buffer.seek(0)
    return SimpleUploadedFile("foto.png", buffer.read(), content_type="image/png")


class ImovelModelTests(TestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)

    def test_corretor_responsavel_precisa_ser_da_mesma_empresa(self):
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        corretor_de_fora = criar_corretor(outra_empresa, email="fora@teste.com")
        imovel = Imovel(
            empresa=self.empresa, codigo="IM001", titulo="Casa",
            corretor_responsavel=corretor_de_fora, proprietario=self.proprietario,
            finalidade=Imovel.Finalidade.VENDA, preco_venda="100000",
        )
        with self.assertRaises(ValidationError):
            imovel.full_clean(exclude=["endereco"])

    def test_responsavel_precisa_ter_perfil_corretor(self):
        gestor = criar_gestor(self.empresa)
        imovel = Imovel(
            empresa=self.empresa, codigo="IM001", titulo="Casa",
            corretor_responsavel=gestor, proprietario=self.proprietario,
            finalidade=Imovel.Finalidade.VENDA, preco_venda="100000",
        )
        with self.assertRaises(ValidationError):
            imovel.full_clean(exclude=["endereco"])

    def test_proprietario_precisa_ser_da_mesma_empresa(self):
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        proprietario_de_fora = criar_proprietario(outra_empresa, documento="10902516612")
        imovel = Imovel(
            empresa=self.empresa, codigo="IM001", titulo="Casa",
            corretor_responsavel=self.corretor, proprietario=proprietario_de_fora,
            finalidade=Imovel.Finalidade.VENDA, preco_venda="100000",
        )
        with self.assertRaises(ValidationError):
            imovel.full_clean(exclude=["endereco"])

    def test_venda_exige_preco_de_venda(self):
        imovel = Imovel(
            empresa=self.empresa, codigo="IM001", titulo="Casa",
            corretor_responsavel=self.corretor, proprietario=self.proprietario,
            finalidade=Imovel.Finalidade.VENDA,
        )
        with self.assertRaises(ValidationError):
            imovel.full_clean(exclude=["endereco"])

    def test_aluguel_exige_valor_do_aluguel(self):
        imovel = Imovel(
            empresa=self.empresa, codigo="IM001", titulo="Casa",
            corretor_responsavel=self.corretor, proprietario=self.proprietario,
            finalidade=Imovel.Finalidade.ALUGUEL,
        )
        with self.assertRaises(ValidationError):
            imovel.full_clean(exclude=["endereco"])

    def test_venda_e_aluguel_exige_os_dois_precos(self):
        imovel = Imovel(
            empresa=self.empresa, codigo="IM001", titulo="Casa",
            corretor_responsavel=self.corretor, proprietario=self.proprietario,
            finalidade=Imovel.Finalidade.VENDA_E_ALUGUEL, preco_venda="100000",
        )
        with self.assertRaises(ValidationError):
            imovel.full_clean(exclude=["endereco"])

        imovel.preco_aluguel = "1500"
        imovel.full_clean(exclude=["endereco"])  # agora não deve levantar erro

    def test_codigo_duplicado_na_mesma_empresa_e_recusado(self):
        criar_imovel(self.empresa, self.corretor, self.proprietario, codigo="IM001")
        duplicado = Imovel(
            empresa=self.empresa, codigo="IM001", titulo="Outra casa",
            corretor_responsavel=self.corretor, proprietario=self.proprietario,
            finalidade=Imovel.Finalidade.VENDA, preco_venda="100000",
        )
        with self.assertRaises(ValidationError):
            duplicado.full_clean(exclude=["endereco"])

    def test_mesmo_codigo_em_empresas_diferentes_e_permitido(self):
        criar_imovel(self.empresa, self.corretor, self.proprietario, codigo="IM001")
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        outro_corretor = criar_corretor(outra_empresa, email="outro@teste.com")
        outro_proprietario = criar_proprietario(outra_empresa, documento="10902516612")
        imovel = Imovel(
            empresa=outra_empresa, codigo="IM001", titulo="Casa",
            corretor_responsavel=outro_corretor, proprietario=outro_proprietario,
            finalidade=Imovel.Finalidade.VENDA, preco_venda="100000",
        )
        imovel.full_clean(exclude=["endereco"])  # não deve levantar erro

    def test_nao_pode_publicar_sem_endereco(self):
        imovel = criar_imovel(self.empresa, self.corretor, self.proprietario)
        self.assertFalse(imovel.pode_publicar())
        with self.assertRaises(ValidationError):
            imovel.publicar()
        self.assertFalse(imovel.publicado)

    def test_nao_pode_publicar_sem_coordenadas(self):
        cidade = Cidade.objects.create(nome="São Paulo", uf="SP")
        endereco = Endereco.objects.create(logradouro="Rua A", cidade=cidade)
        imovel = criar_imovel(self.empresa, self.corretor, self.proprietario, endereco=endereco)
        self.assertFalse(imovel.pode_publicar())
        with self.assertRaises(ValidationError):
            imovel.publicar()

    def test_publica_com_endereco_e_coordenadas_completos(self):
        cidade = Cidade.objects.create(nome="São Paulo", uf="SP")
        endereco = Endereco.objects.create(
            logradouro="Rua A", cidade=cidade, latitude="-23.55", longitude="-46.63"
        )
        imovel = criar_imovel(self.empresa, self.corretor, self.proprietario, endereco=endereco)
        self.assertTrue(imovel.pode_publicar())
        imovel.publicar()
        self.assertTrue(imovel.publicado)


class FotoImovelModelTests(TestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.imovel = criar_imovel(self.empresa, self.corretor, self.proprietario)

    def test_apenas_uma_foto_capa_por_imovel(self):
        FotoImovel.objects.create(imovel=self.imovel, imagem=gerar_imagem(), ordem=0, capa=True)
        with self.assertRaises(IntegrityError):
            FotoImovel.objects.create(imovel=self.imovel, imagem=gerar_imagem(), ordem=1, capa=True)

    def test_definir_como_capa_desmarca_a_anterior(self):
        primeira = FotoImovel.objects.create(imovel=self.imovel, imagem=gerar_imagem(), ordem=0, capa=True)
        segunda = FotoImovel.objects.create(imovel=self.imovel, imagem=gerar_imagem(), ordem=1)

        segunda.definir_como_capa()

        primeira.refresh_from_db()
        segunda.refresh_from_db()
        self.assertFalse(primeira.capa)
        self.assertTrue(segunda.capa)


class PainelImovelTests(TestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.cidade = Cidade.objects.create(nome="São Paulo", uf="SP")

    def test_administrador_nao_acessa(self):
        admin = Usuario.objects.create_superuser(
            email="admin@teste.com", senha="senha-forte-123", nome="Admin"
        )
        self.client.force_login(admin)
        response = self.client.get(reverse("imoveis:imovel_list"))
        self.assertEqual(response.status_code, 403)

    def test_gestor_nao_acessa(self):
        gestor = criar_gestor(self.empresa)
        self.client.force_login(gestor)
        response = self.client.get(reverse("imoveis:imovel_list"))
        self.assertEqual(response.status_code, 403)

    def test_corretor_cadastra_imovel(self):
        self.client.force_login(self.corretor)
        response = self.client.post(reverse("imoveis:imovel_create"), {
            "codigo": "IM001", "titulo": "Casa bonita",
            "corretor_responsavel": self.corretor.pk, "proprietario": self.proprietario.pk,
            "finalidade": Imovel.Finalidade.VENDA, "preco_venda": "300000",
            "preco_aluguel": "", "preco_condominio": "", "preco_iptu": "", "descricao": "",
        })
        imovel = Imovel.objects.get(codigo="IM001")
        self.assertRedirects(response, reverse("imoveis:imovel_detail", args=[imovel.pk]))
        self.assertEqual(imovel.empresa, self.empresa)

    def test_lista_mostra_apenas_imoveis_da_propria_empresa(self):
        criar_imovel(self.empresa, self.corretor, self.proprietario, codigo="IM001")
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        outro_corretor = criar_corretor(outra_empresa, email="outro@teste.com")
        outro_proprietario = criar_proprietario(outra_empresa, documento="10902516612")
        criar_imovel(outra_empresa, outro_corretor, outro_proprietario, codigo="IM002")

        self.client.force_login(self.corretor)
        response = self.client.get(reverse("imoveis:imovel_list"))
        codigos = [i.codigo for i in response.context["imoveis"]]
        self.assertEqual(codigos, ["IM001"])

    def test_atualizar_endereco_e_publicar(self):
        imovel = criar_imovel(self.empresa, self.corretor, self.proprietario)
        self.client.force_login(self.corretor)

        self.client.post(reverse("imoveis:imovel_atualizar_endereco", args=[imovel.pk]), {
            "cep": "01000-000", "logradouro": "Rua A", "numero": "10", "complemento": "",
            "bairro": "Centro", "cidade": self.cidade.pk, "latitude": "-23.55", "longitude": "-46.63",
        })
        imovel.refresh_from_db()
        self.assertIsNotNone(imovel.endereco)
        self.assertIsNotNone(imovel.endereco.latitude)

        response = self.client.post(reverse("imoveis:imovel_publicar", args=[imovel.pk]))
        imovel.refresh_from_db()
        self.assertTrue(imovel.publicado)

    def test_publicar_falha_sem_endereco(self):
        imovel = criar_imovel(self.empresa, self.corretor, self.proprietario)
        self.client.force_login(self.corretor)
        self.client.post(reverse("imoveis:imovel_publicar", args=[imovel.pk]))
        imovel.refresh_from_db()
        self.assertFalse(imovel.publicado)

    def test_upload_e_definir_capa(self):
        imovel = criar_imovel(self.empresa, self.corretor, self.proprietario)
        self.client.force_login(self.corretor)

        self.client.post(
            reverse("imoveis:imovel_upload_fotos", args=[imovel.pk]),
            {"fotos": [gerar_imagem(), gerar_imagem()]},
            format="multipart",
        )
        self.assertEqual(imovel.fotos.count(), 2)

        primeira_foto = imovel.fotos.first()
        self.client.post(reverse("imoveis:imovel_definir_capa", args=[imovel.pk, primeira_foto.pk]))
        primeira_foto.refresh_from_db()
        self.assertTrue(primeira_foto.capa)


class ImovelApiTests(APITestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.cidade = Cidade.objects.create(nome="São Paulo", uf="SP")

        self.outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        self.outro_corretor = criar_corretor(self.outra_empresa, email="outro@teste.com")
        self.outro_proprietario = criar_proprietario(self.outra_empresa, documento="10902516612")
        self.imovel_de_fora = criar_imovel(
            self.outra_empresa, self.outro_corretor, self.outro_proprietario, codigo="FORA"
        )

    def test_gestor_nao_acessa_api(self):
        gestor = criar_gestor(self.empresa)
        self.client.force_authenticate(gestor)
        response = self.client.get(reverse("imovel-list"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_lista_apenas_da_propria_empresa(self):
        criar_imovel(self.empresa, self.corretor, self.proprietario, codigo="IM001")
        self.client.force_authenticate(self.corretor)
        response = self.client.get(reverse("imovel-list"))
        codigos = [item["codigo"] for item in response.data]
        self.assertEqual(codigos, ["IM001"])

    def test_trocar_id_na_url_nao_abre_imovel_de_outra_empresa(self):
        self.client.force_authenticate(self.corretor)
        response = self.client.get(reverse("imovel-detail", args=[self.imovel_de_fora.pk]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_criar_imovel_valido(self):
        self.client.force_authenticate(self.corretor)
        response = self.client.post(reverse("imovel-list"), {
            "codigo": "IM001", "titulo": "Casa bonita",
            "corretor_responsavel": self.corretor.pk, "proprietario": self.proprietario.pk,
            "finalidade": Imovel.Finalidade.VENDA, "preco_venda": "300000",
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_criar_imovel_venda_sem_preco_e_recusado(self):
        self.client.force_authenticate(self.corretor)
        response = self.client.post(reverse("imovel-list"), {
            "codigo": "IM001", "titulo": "Casa bonita",
            "corretor_responsavel": self.corretor.pk, "proprietario": self.proprietario.pk,
            "finalidade": Imovel.Finalidade.VENDA,
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_endereco_via_api_e_publicar(self):
        imovel = criar_imovel(self.empresa, self.corretor, self.proprietario)
        self.client.force_authenticate(self.corretor)

        url_endereco = reverse("imovel-endereco", args=[imovel.pk])
        response = self.client.put(url_endereco, {
            "logradouro": "Rua A", "cidade": self.cidade.pk, "latitude": "-23.55", "longitude": "-46.63",
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        url_publicar = reverse("imovel-publicar", args=[imovel.pk])
        response = self.client.post(url_publicar)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["publicado"])

    def test_publicar_sem_endereco_e_recusado(self):
        imovel = criar_imovel(self.empresa, self.corretor, self.proprietario)
        self.client.force_authenticate(self.corretor)
        response = self.client.post(reverse("imovel-publicar", args=[imovel.pk]))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upload_de_varias_fotos_de_uma_vez(self):
        imovel = criar_imovel(self.empresa, self.corretor, self.proprietario)
        self.client.force_authenticate(self.corretor)

        response = self.client.post(reverse("foto-imovel-list"), {
            "imovel": imovel.pk,
            "imagens": [gerar_imagem(), gerar_imagem(), gerar_imagem()],
        }, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data), 3)
        self.assertEqual(imovel.fotos.count(), 3)

    def test_definir_capa_via_api(self):
        imovel = criar_imovel(self.empresa, self.corretor, self.proprietario)
        foto = FotoImovel.objects.create(imovel=imovel, imagem=gerar_imagem(), ordem=0)
        self.client.force_authenticate(self.corretor)

        response = self.client.post(reverse("foto-imovel-definir-capa", args=[foto.pk]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        foto.refresh_from_db()
        self.assertTrue(foto.capa)

    def test_nao_ve_foto_de_imovel_de_outra_empresa(self):
        foto_de_fora = FotoImovel.objects.create(
            imovel=self.imovel_de_fora, imagem=gerar_imagem(), ordem=0
        )
        self.client.force_authenticate(self.corretor)
        response = self.client.get(reverse("foto-imovel-detail", args=[foto_de_fora.pk]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
