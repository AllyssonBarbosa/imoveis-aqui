import io
from decimal import Decimal

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

from .models import (
    Apartamento,
    Caracteristica,
    Casa,
    FotoImovel,
    Imovel,
    Lote,
    Loteamento,
    Predio,
    Quadra,
    Terreno,
)


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
        empresa=empresa, codigo=codigo, titulo="Casa bonita", natureza=Imovel.Natureza.CASA,
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
            empresa=self.empresa, codigo="IM001", titulo="Casa", natureza=Imovel.Natureza.CASA,
            corretor_responsavel=corretor_de_fora, proprietario=self.proprietario,
            finalidade=Imovel.Finalidade.VENDA, preco_venda="100000",
        )
        with self.assertRaises(ValidationError):
            imovel.full_clean(exclude=["endereco"])

    def test_responsavel_precisa_ter_perfil_corretor(self):
        gestor = criar_gestor(self.empresa)
        imovel = Imovel(
            empresa=self.empresa, codigo="IM001", titulo="Casa", natureza=Imovel.Natureza.CASA,
            corretor_responsavel=gestor, proprietario=self.proprietario,
            finalidade=Imovel.Finalidade.VENDA, preco_venda="100000",
        )
        with self.assertRaises(ValidationError):
            imovel.full_clean(exclude=["endereco"])

    def test_proprietario_precisa_ser_da_mesma_empresa(self):
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        proprietario_de_fora = criar_proprietario(outra_empresa, documento="10902516612")
        imovel = Imovel(
            empresa=self.empresa, codigo="IM001", titulo="Casa", natureza=Imovel.Natureza.CASA,
            corretor_responsavel=self.corretor, proprietario=proprietario_de_fora,
            finalidade=Imovel.Finalidade.VENDA, preco_venda="100000",
        )
        with self.assertRaises(ValidationError):
            imovel.full_clean(exclude=["endereco"])

    def test_venda_exige_preco_de_venda(self):
        imovel = Imovel(
            empresa=self.empresa, codigo="IM001", titulo="Casa", natureza=Imovel.Natureza.CASA,
            corretor_responsavel=self.corretor, proprietario=self.proprietario,
            finalidade=Imovel.Finalidade.VENDA,
        )
        with self.assertRaises(ValidationError):
            imovel.full_clean(exclude=["endereco"])

    def test_aluguel_exige_valor_do_aluguel(self):
        imovel = Imovel(
            empresa=self.empresa, codigo="IM001", titulo="Casa", natureza=Imovel.Natureza.CASA,
            corretor_responsavel=self.corretor, proprietario=self.proprietario,
            finalidade=Imovel.Finalidade.ALUGUEL,
        )
        with self.assertRaises(ValidationError):
            imovel.full_clean(exclude=["endereco"])

    def test_venda_e_aluguel_exige_os_dois_precos(self):
        imovel = Imovel(
            empresa=self.empresa, codigo="IM001", titulo="Casa", natureza=Imovel.Natureza.CASA,
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
            empresa=self.empresa, codigo="IM001", titulo="Outra casa", natureza=Imovel.Natureza.CASA,
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
            empresa=outra_empresa, codigo="IM001", titulo="Casa", natureza=Imovel.Natureza.CASA,
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
            "codigo": "IM001", "titulo": "Casa bonita", "natureza": Imovel.Natureza.CASA,
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
            "codigo": "IM001", "titulo": "Casa bonita", "natureza": Imovel.Natureza.CASA,
            "corretor_responsavel": self.corretor.pk, "proprietario": self.proprietario.pk,
            "finalidade": Imovel.Finalidade.VENDA, "preco_venda": "300000",
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_criar_imovel_venda_sem_preco_e_recusado(self):
        self.client.force_authenticate(self.corretor)
        response = self.client.post(reverse("imovel-list"), {
            "codigo": "IM001", "titulo": "Casa bonita", "natureza": Imovel.Natureza.CASA,
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


DADOS_CASA = {
    "area_terreno": "300.00", "area_construida": "180.00", "quartos": 3, "suites": 1,
    "banheiros": 2, "salas": 1, "vagas": 2, "pavimentos": 2, "ano_construcao": 2015,
    "piscina": True, "quintal": True, "churrasqueira": False, "mobiliada": False,
}

DADOS_TERRENO = {
    "area_total": "360.00", "frente": "12.00", "fundo": "12.00",
    "lateral_esquerda": "30.00", "lateral_direita": "30.00",
    "topografia": Terreno.Topografia.PLANO, "murado": True, "esquina": False,
    "uso_permitido": Terreno.UsoPermitido.RESIDENCIAL,
}


def dados_de_formulario(dados):
    # checkbox desmarcado não vai no POST
    return {campo: valor for campo, valor in dados.items() if valor is not False}


class CasaTerrenoModelTests(TestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.casa = criar_imovel(self.empresa, self.corretor, self.proprietario, codigo="CASA")
        self.terreno = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="TER",
            natureza=Imovel.Natureza.TERRENO,
        )

    def test_area_livre_e_calculada(self):
        casa = Casa(imovel=self.casa, **DADOS_CASA)
        casa.full_clean()
        casa.save()
        casa.refresh_from_db()
        self.assertEqual(casa.area_livre, Decimal("120.00"))

    def test_area_livre_acompanha_mudanca_da_area_construida(self):
        casa = Casa.objects.create(imovel=self.casa, **DADOS_CASA)
        casa.refresh_from_db()
        casa.area_construida = Decimal("250.00")
        self.assertEqual(casa.area_livre, Decimal("50.00"))

    def test_area_livre_nao_e_campo_do_banco(self):
        nomes = [campo.name for campo in Casa._meta.get_fields()]
        self.assertNotIn("area_livre", nomes)

    def test_area_construida_maior_que_terreno_e_recusada_com_mensagem_clara(self):
        casa = Casa(imovel=self.casa, **{**DADOS_CASA, "area_construida": "350.00"})
        with self.assertRaises(ValidationError) as contexto:
            casa.full_clean()
        mensagem = contexto.exception.message_dict["area_construida"][0]
        self.assertIn("não pode ser maior que a área do terreno", mensagem)

    def test_area_construida_igual_ao_terreno_e_aceita(self):
        casa = Casa(imovel=self.casa, **{**DADOS_CASA, "area_construida": "300.00"})
        casa.full_clean()

    def test_banco_tambem_recusa_area_construida_maior(self):
        with self.assertRaises(IntegrityError):
            Casa.objects.create(imovel=self.casa, **{**DADOS_CASA, "area_construida": "350.00"})

    def test_suites_nao_passam_de_quartos(self):
        casa = Casa(imovel=self.casa, **{**DADOS_CASA, "quartos": 1, "suites": 2})
        with self.assertRaises(ValidationError):
            casa.full_clean()

    def test_dados_de_casa_nao_entram_em_terreno(self):
        casa = Casa(imovel=self.terreno, **DADOS_CASA)
        with self.assertRaises(ValidationError):
            casa.full_clean()

    def test_dados_de_terreno_nao_entram_em_casa(self):
        terreno = Terreno(imovel=self.casa, **DADOS_TERRENO)
        with self.assertRaises(ValidationError):
            terreno.full_clean()

    def test_cadastro_de_terreno(self):
        terreno = Terreno(imovel=self.terreno, **DADOS_TERRENO)
        terreno.full_clean()
        terreno.save()
        self.assertEqual(self.terreno.detalhes, terreno)


class PainelCasaTerrenoTests(TestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.casa = criar_imovel(self.empresa, self.corretor, self.proprietario, codigo="CASA")
        self.terreno = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="TER",
            natureza=Imovel.Natureza.TERRENO,
        )
        self.client.force_login(self.corretor)

    def test_tela_da_casa_mostra_so_campos_de_casa(self):
        response = self.client.get(reverse("imoveis:imovel_detail", args=[self.casa.pk]))
        campos = set(response.context["detalhes_form"].fields)
        self.assertIn("area_construida", campos)
        self.assertNotIn("topografia", campos)
        self.assertNotIn("area_livre", campos)
        self.assertNotContains(response, 'name="topografia"')

    def test_tela_sem_dados_ainda_mostra_que_falta_preencher(self):
        response = self.client.get(reverse("imoveis:imovel_detail", args=[self.casa.pk]))
        self.assertContains(response, "Ainda não preenchido")
        self.assertNotContains(response, "None m²")

    def test_tela_do_terreno_mostra_so_campos_de_terreno(self):
        response = self.client.get(reverse("imoveis:imovel_detail", args=[self.terreno.pk]))
        campos = set(response.context["detalhes_form"].fields)
        self.assertIn("topografia", campos)
        self.assertNotIn("quartos", campos)
        self.assertNotContains(response, 'name="quartos"')

    def test_salvar_dados_da_casa(self):
        response = self.client.post(
            reverse("imoveis:imovel_atualizar_detalhes", args=[self.casa.pk]),
            dados_de_formulario(DADOS_CASA),
        )
        self.assertRedirects(response, reverse("imoveis:imovel_detail", args=[self.casa.pk]))
        self.assertEqual(Casa.objects.get(imovel=self.casa).area_livre, Decimal("120.00"))

    def test_area_livre_digitada_e_ignorada(self):
        self.client.post(
            reverse("imoveis:imovel_atualizar_detalhes", args=[self.casa.pk]),
            {**dados_de_formulario(DADOS_CASA), "area_livre": "999"},
        )
        self.assertEqual(Casa.objects.get(imovel=self.casa).area_livre, Decimal("120.00"))

    def test_area_construida_maior_mostra_erro_na_tela(self):
        response = self.client.post(
            reverse("imoveis:imovel_atualizar_detalhes", args=[self.casa.pk]),
            {**dados_de_formulario(DADOS_CASA), "area_construida": "350"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "não pode ser maior que a área do terreno")
        self.assertFalse(Casa.objects.filter(imovel=self.casa).exists())

    def test_salvar_dados_do_terreno(self):
        self.client.post(
            reverse("imoveis:imovel_atualizar_detalhes", args=[self.terreno.pk]),
            dados_de_formulario(DADOS_TERRENO),
        )
        terreno = Terreno.objects.get(imovel=self.terreno)
        self.assertEqual(terreno.topografia, Terreno.Topografia.PLANO)
        self.assertTrue(terreno.murado)

    def test_nao_salva_detalhes_de_imovel_de_outra_empresa(self):
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        outro_corretor = criar_corretor(outra_empresa, email="outro@teste.com")
        outro_proprietario = criar_proprietario(outra_empresa, documento="10902516612")
        de_fora = criar_imovel(outra_empresa, outro_corretor, outro_proprietario, codigo="FORA")
        response = self.client.post(
            reverse("imoveis:imovel_atualizar_detalhes", args=[de_fora.pk]),
            dados_de_formulario(DADOS_CASA),
        )
        self.assertEqual(response.status_code, 404)


class ApiCasaTerrenoTests(APITestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.casa = criar_imovel(self.empresa, self.corretor, self.proprietario, codigo="CASA")
        self.terreno = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="TER",
            natureza=Imovel.Natureza.TERRENO,
        )
        self.client.force_authenticate(self.corretor)

    def test_detalhe_da_casa_traz_dados_e_area_livre(self):
        Casa.objects.create(imovel=self.casa, **DADOS_CASA)
        response = self.client.get(reverse("imovel-detail", args=[self.casa.pk]))
        self.assertEqual(response.data["natureza"], "CASA")
        self.assertEqual(response.data["casa"]["quartos"], 3)
        self.assertEqual(response.data["casa"]["area_livre"], "120.00")
        self.assertIsNone(response.data["terreno"])

    def test_detalhe_do_terreno_traz_dados(self):
        Terreno.objects.create(imovel=self.terreno, **DADOS_TERRENO)
        response = self.client.get(reverse("imovel-detail", args=[self.terreno.pk]))
        self.assertEqual(response.data["terreno"]["topografia"], "PLANO")
        self.assertIsNone(response.data["casa"])

    def test_put_detalhes_da_casa(self):
        response = self.client.put(
            reverse("imovel-detalhes", args=[self.casa.pk]), DADOS_CASA, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["area_livre"], "120.00")

    def test_area_livre_enviada_pela_api_e_ignorada(self):
        response = self.client.put(
            reverse("imovel-detalhes", args=[self.casa.pk]),
            {**DADOS_CASA, "area_livre": "999.00"}, format="json",
        )
        self.assertEqual(response.data["area_livre"], "120.00")

    def test_put_area_construida_maior_e_recusado(self):
        response = self.client.put(
            reverse("imovel-detalhes", args=[self.casa.pk]),
            {**DADOS_CASA, "area_construida": "350.00"}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("não pode ser maior", str(response.data["area_construida"][0]))

    def test_put_detalhes_do_terreno_usa_campos_de_terreno(self):
        response = self.client.put(
            reverse("imovel-detalhes", args=[self.terreno.pk]), DADOS_CASA, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("topografia", response.data)

    def test_natureza_nao_pode_ser_trocada(self):
        response = self.client.patch(
            reverse("imovel-detail", args=[self.casa.pk]),
            {"natureza": Imovel.Natureza.TERRENO}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_detalhes_de_outra_empresa_da_404(self):
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        outro_corretor = criar_corretor(outra_empresa, email="outro@teste.com")
        outro_proprietario = criar_proprietario(outra_empresa, documento="10902516612")
        de_fora = criar_imovel(outra_empresa, outro_corretor, outro_proprietario, codigo="FORA")
        response = self.client.put(
            reverse("imovel-detalhes", args=[de_fora.pk]), DADOS_CASA, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


def criar_predio(empresa, nome="Edifício Aurora", cidade=None, **extra):
    cidade = cidade or Cidade.objects.get_or_create(nome="São Paulo", uf="SP")[0]
    endereco = Endereco.objects.create(
        logradouro="Av. Paulista", numero="1000", cidade=cidade,
        latitude="-23.56", longitude="-46.65",
    )
    dados = dict(
        empresa=empresa, nome=nome, endereco=endereco, andares=10, unidades_por_andar=4,
        ano_construcao=2010, elevadores=2, portaria_24h=True, piscina=True,
        valor_condominio_base="800.00",
    )
    dados.update(extra)
    return Predio.objects.create(**dados)


def dados_apartamento(predio, **extra):
    dados = {
        "predio": predio.pk, "andar": 3, "numero": "31", "area_privativa": "70.00",
        "area_total": "90.00", "quartos": 2, "suites": 1, "banheiros": 2, "vagas": 1,
        "varanda": True, "posicao_solar": Apartamento.PosicaoSolar.NASCENTE,
    }
    dados.update(extra)
    return dados


def criar_apartamento(imovel, predio, **extra):
    dados = dados_apartamento(predio, **extra)
    dados["predio"] = predio
    return Apartamento.objects.create(imovel=imovel, **dados)


class PredioApartamentoModelTests(TestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.predio = criar_predio(self.empresa)
        self.apto = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="AP1",
            natureza=Imovel.Natureza.APARTAMENTO,
        )
        self.apto2 = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="AP2",
            natureza=Imovel.Natureza.APARTAMENTO,
        )

    def _apartamento(self, imovel, **extra):
        dados = dados_apartamento(self.predio, **extra)
        dados["predio"] = self.predio
        return Apartamento(imovel=imovel, **dados)

    def test_cadastro_de_apartamento_ligado_ao_predio(self):
        apartamento = self._apartamento(self.apto)
        apartamento.full_clean()
        apartamento.save()
        self.assertEqual(self.predio.apartamentos.count(), 1)

    def test_mesmo_andar_e_numero_no_mesmo_predio_e_recusado(self):
        criar_apartamento(self.apto, self.predio)
        duplicado = self._apartamento(self.apto2)
        with self.assertRaises(ValidationError) as contexto:
            duplicado.full_clean()
        self.assertIn(
            "Já existe um apartamento com esse andar e número neste prédio.",
            contexto.exception.messages,
        )

    def test_banco_tambem_recusa_andar_e_numero_repetidos(self):
        criar_apartamento(self.apto, self.predio)
        with self.assertRaises(IntegrityError):
            criar_apartamento(self.apto2, self.predio)

    def test_mesmo_numero_em_outro_andar_e_aceito(self):
        criar_apartamento(self.apto, self.predio)
        self._apartamento(self.apto2, andar=4).full_clean()

    def test_mesmo_andar_e_numero_em_outro_predio_e_aceito(self):
        criar_apartamento(self.apto, self.predio)
        outro_predio = criar_predio(self.empresa, nome="Edifício Boreal")
        apartamento = self._apartamento(self.apto2)
        apartamento.predio = outro_predio
        apartamento.full_clean()

    def test_andar_acima_do_predio_e_recusado(self):
        with self.assertRaises(ValidationError) as contexto:
            self._apartamento(self.apto, andar=11).full_clean()
        self.assertIn("andar", contexto.exception.message_dict)

    def test_area_privativa_maior_que_total_e_recusada(self):
        with self.assertRaises(ValidationError) as contexto:
            self._apartamento(self.apto, area_privativa="100.00").full_clean()
        self.assertIn("area_privativa", contexto.exception.message_dict)

    def test_predio_de_outra_empresa_e_recusado(self):
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        predio_de_fora = criar_predio(outra_empresa)
        apartamento = self._apartamento(self.apto)
        apartamento.predio = predio_de_fora
        with self.assertRaises(ValidationError) as contexto:
            apartamento.full_clean()
        self.assertIn("predio", contexto.exception.message_dict)

    def test_apartamento_nao_entra_em_casa(self):
        casa = criar_imovel(self.empresa, self.corretor, self.proprietario, codigo="CASA")
        with self.assertRaises(ValidationError):
            self._apartamento(casa).full_clean()

    def test_nome_de_predio_repetido_na_empresa_e_recusado(self):
        repetido = Predio(
            empresa=self.empresa, nome="Edifício Aurora", andares=5, unidades_por_andar=2,
        )
        with self.assertRaises(ValidationError):
            repetido.full_clean(exclude=["endereco"])

    def test_apartamento_usa_endereco_do_predio_para_publicar(self):
        criar_apartamento(self.apto, self.predio)
        self.assertEqual(self.apto.endereco_do_anuncio, self.predio.endereco)
        self.assertTrue(self.apto.pode_publicar())

    def test_apartamento_sem_predio_ainda_nao_publica(self):
        self.assertFalse(self.apto.pode_publicar())

    def test_alterar_predio_reflete_em_todas_as_unidades(self):
        criar_apartamento(self.apto, self.predio)
        criar_apartamento(self.apto2, self.predio, numero="32")

        self.predio.academia = True
        self.predio.save()

        for imovel in (self.apto, self.apto2):
            imovel = Imovel.objects.get(pk=imovel.pk)
            self.assertTrue(imovel.detalhes.predio.academia)


class PainelPredioApartamentoTests(TestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.cidade = Cidade.objects.create(nome="São Paulo", uf="SP")
        self.predio = criar_predio(self.empresa, cidade=self.cidade)
        self.apto = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="AP1",
            natureza=Imovel.Natureza.APARTAMENTO,
        )
        self.client.force_login(self.corretor)

    def _post_predio(self, url, **extra):
        dados = {
            "nome": "Edifício Novo", "andares": 8, "unidades_por_andar": 2,
            "ano_construcao": 2018, "elevadores": 1, "portaria_24h": "on", "piscina": "on",
            "valor_condominio_base": "650.00",
            "endereco-logradouro": "Rua B", "endereco-numero": "20", "endereco-cep": "",
            "endereco-complemento": "", "endereco-bairro": "Centro",
            "endereco-cidade": self.cidade.pk, "endereco-latitude": "-23.5",
            "endereco-longitude": "-46.6",
        }
        dados.update(extra)
        return self.client.post(url, dados)

    def test_gestor_nao_acessa_predios(self):
        self.client.force_login(criar_gestor(self.empresa))
        response = self.client.get(reverse("imoveis:predio_list"))
        self.assertEqual(response.status_code, 403)

    def test_cadastra_predio_com_endereco(self):
        response = self._post_predio(reverse("imoveis:predio_create"))
        self.assertRedirects(response, reverse("imoveis:predio_list"))
        predio = Predio.objects.get(nome="Edifício Novo")
        self.assertEqual(predio.empresa, self.empresa)
        self.assertEqual(predio.endereco.logradouro, "Rua B")
        self.assertTrue(predio.portaria_24h)

    def test_nome_repetido_mostra_erro(self):
        response = self._post_predio(reverse("imoveis:predio_create"), nome="Edifício Aurora")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Já existe um prédio com esse nome")

    def test_lista_so_predios_da_propria_empresa(self):
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        criar_predio(outra_empresa, nome="Prédio de fora")
        response = self.client.get(reverse("imoveis:predio_list"))
        nomes = [p.nome for p in response.context["predios"]]
        self.assertEqual(nomes, ["Edifício Aurora"])

    def test_editar_predio_de_outra_empresa_da_404(self):
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        de_fora = criar_predio(outra_empresa, nome="Prédio de fora")
        response = self.client.get(reverse("imoveis:predio_update", args=[de_fora.pk]))
        self.assertEqual(response.status_code, 404)

    def test_editar_predio_reflete_no_detalhe_do_apartamento(self):
        criar_apartamento(self.apto, self.predio)
        self._post_predio(
            reverse("imoveis:predio_update", args=[self.predio.pk]),
            nome="Edifício Aurora", academia="on", **{"endereco-logradouro": "Rua Corrigida"},
        )
        response = self.client.get(reverse("imoveis:imovel_detail", args=[self.apto.pk]))
        self.assertContains(response, "Academia")
        self.assertContains(response, "Rua Corrigida")
        self.assertEqual(Endereco.objects.filter(logradouro="Rua Corrigida").count(), 1)

    def test_tela_do_apartamento_so_lista_predios_da_empresa(self):
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        criar_predio(outra_empresa, nome="Prédio de fora")
        response = self.client.get(reverse("imoveis:imovel_detail", args=[self.apto.pk]))
        form = response.context["detalhes_form"]
        self.assertEqual(list(form.fields["predio"].queryset), [self.predio])
        self.assertNotIn("area_terreno", form.fields)

    def test_salva_apartamento_e_detalhe_mostra_dados_do_predio(self):
        dados = dados_de_formulario(dados_apartamento(self.predio))
        response = self.client.post(
            reverse("imoveis:imovel_atualizar_detalhes", args=[self.apto.pk]), dados
        )
        self.assertRedirects(response, reverse("imoveis:imovel_detail", args=[self.apto.pk]))
        response = self.client.get(reverse("imoveis:imovel_detail", args=[self.apto.pk]))
        self.assertContains(response, "Edifício Aurora")
        self.assertContains(response, "Portaria 24h")
        self.assertContains(response, "Av. Paulista")

    def test_apartamento_repetido_mostra_mensagem_clara(self):
        outro = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="AP2",
            natureza=Imovel.Natureza.APARTAMENTO,
        )
        criar_apartamento(outro, self.predio)
        response = self.client.post(
            reverse("imoveis:imovel_atualizar_detalhes", args=[self.apto.pk]),
            dados_de_formulario(dados_apartamento(self.predio)),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Já existe um apartamento com esse andar e número neste prédio.")

    def test_apartamento_nao_tem_endereco_proprio(self):
        self.client.post(reverse("imoveis:imovel_atualizar_endereco", args=[self.apto.pk]), {
            "logradouro": "Rua X", "cidade": self.cidade.pk,
        })
        self.apto.refresh_from_db()
        self.assertIsNone(self.apto.endereco)


class ApiPredioApartamentoTests(APITestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.cidade = Cidade.objects.create(nome="São Paulo", uf="SP")
        self.predio = criar_predio(self.empresa, cidade=self.cidade)
        self.apto = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="AP1",
            natureza=Imovel.Natureza.APARTAMENTO,
        )
        self.outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        self.predio_de_fora = criar_predio(self.outra_empresa, nome="Prédio de fora")
        self.client.force_authenticate(self.corretor)

    def test_cria_predio_com_endereco(self):
        response = self.client.post(reverse("predio-list"), {
            "nome": "Edifício Novo", "andares": 5, "unidades_por_andar": 2, "elevadores": 1,
            "academia": True,
            "endereco": {"logradouro": "Rua B", "cidade": self.cidade.pk},
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        predio = Predio.objects.get(pk=response.data["id"])
        self.assertEqual(predio.empresa, self.empresa)
        self.assertEqual(predio.endereco.logradouro, "Rua B")

    def test_nome_repetido_e_recusado(self):
        response = self.client.post(reverse("predio-list"), {
            "nome": "Edifício Aurora", "andares": 5, "unidades_por_andar": 2,
            "endereco": {"logradouro": "Rua B", "cidade": self.cidade.pk},
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_lista_so_predios_da_propria_empresa(self):
        response = self.client.get(reverse("predio-list"))
        self.assertEqual([p["nome"] for p in response.data], ["Edifício Aurora"])

    def test_predio_de_outra_empresa_da_404(self):
        response = self.client.get(reverse("predio-detail", args=[self.predio_de_fora.pk]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_put_detalhes_do_apartamento(self):
        response = self.client.put(
            reverse("imovel-detalhes", args=[self.apto.pk]), dados_apartamento(self.predio),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["predio"]["nome"], "Edifício Aurora")

    def test_detalhe_do_apartamento_traz_dados_do_predio(self):
        criar_apartamento(self.apto, self.predio)
        response = self.client.get(reverse("imovel-detail", args=[self.apto.pk]))
        apartamento = response.data["apartamento"]
        self.assertEqual(apartamento["numero"], "31")
        self.assertEqual(apartamento["predio"]["nome"], "Edifício Aurora")
        self.assertTrue(apartamento["predio"]["portaria_24h"])
        self.assertEqual(apartamento["predio"]["endereco"]["logradouro"], "Av. Paulista")
        self.assertEqual(response.data["endereco"]["logradouro"], "Av. Paulista")
        self.assertIsNone(response.data["casa"])

    def test_alterar_predio_reflete_no_detalhe_de_todos_os_apartamentos(self):
        outro = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="AP2",
            natureza=Imovel.Natureza.APARTAMENTO,
        )
        criar_apartamento(self.apto, self.predio)
        criar_apartamento(outro, self.predio, numero="32")

        response = self.client.patch(reverse("predio-detail", args=[self.predio.pk]), {
            "playground": True, "endereco": {"logradouro": "Rua Corrigida", "cidade": self.cidade.pk},
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        for imovel in (self.apto, outro):
            detalhe = self.client.get(reverse("imovel-detail", args=[imovel.pk])).data
            self.assertTrue(detalhe["apartamento"]["predio"]["playground"])
            self.assertEqual(detalhe["endereco"]["logradouro"], "Rua Corrigida")

    def test_apartamento_repetido_e_recusado_com_mensagem(self):
        outro = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="AP2",
            natureza=Imovel.Natureza.APARTAMENTO,
        )
        criar_apartamento(outro, self.predio)
        response = self.client.put(
            reverse("imovel-detalhes", args=[self.apto.pk]), dados_apartamento(self.predio),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            "Já existe um apartamento com esse andar e número neste prédio.",
            [str(m) for m in response.data["non_field_errors"]],
        )

    def test_nao_liga_apartamento_a_predio_de_outra_empresa(self):
        response = self.client.put(
            reverse("imovel-detalhes", args=[self.apto.pk]),
            dados_apartamento(self.predio_de_fora), format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("predio", response.data)

    def test_nao_exclui_predio_com_apartamentos(self):
        criar_apartamento(self.apto, self.predio)
        response = self.client.delete(reverse("predio-detail", args=[self.predio.pk]))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(Predio.objects.filter(pk=self.predio.pk).exists())

    def test_endereco_proprio_do_apartamento_e_recusado(self):
        response = self.client.put(reverse("imovel-endereco", args=[self.apto.pk]), {
            "logradouro": "Rua X", "cidade": self.cidade.pk,
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


def criar_loteamento(empresa, nome="Jardim das Flores", **extra):
    cidade = Cidade.objects.get_or_create(nome="São Paulo", uf="SP")[0]
    dados = dict(
        empresa=empresa, nome=nome, cidade=cidade, incorporadora="Incorporadora Alfa",
        asfalto=True, agua=True, energia=True, esgoto=False, iluminacao=True,
        areas_de_lazer="Praça e quadra poliesportiva",
    )
    dados.update(extra)
    return Loteamento.objects.create(**dados)


def criar_imovel_lote(empresa, corretor, proprietario, codigo, **extra):
    imovel = criar_imovel(
        empresa, corretor, proprietario, codigo=codigo, natureza=Imovel.Natureza.LOTE,
        preco_venda="120000.00", **extra,
    )
    imovel.refresh_from_db()  # preço volta como Decimal, como chega pelas telas e pela API
    return imovel


def dados_lote(quadra, **extra):
    dados = {
        "quadra": quadra.pk, "numero": "7", "area": "250.00", "frente": "10.00",
        "fundo": "25.00", "entrada_minima": "12000.00", "parcelas_maximas": 120,
        "condicoes_parcelamento": "Correção anual pelo IPCA",
    }
    dados.update(extra)
    return dados


def criar_lote(imovel, quadra, **extra):
    dados = dados_lote(quadra, **extra)
    dados["quadra"] = quadra
    return Lote.objects.create(imovel=imovel, **dados)


class LoteamentoModelTests(TestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.loteamento = criar_loteamento(self.empresa)
        self.quadra_a = Quadra.objects.create(loteamento=self.loteamento, identificacao="A")
        self.imovel1 = criar_imovel_lote(self.empresa, self.corretor, self.proprietario, "L1")
        self.imovel2 = criar_imovel_lote(self.empresa, self.corretor, self.proprietario, "L2")

    def _lote(self, imovel, **extra):
        dados = dados_lote(self.quadra_a, **extra)
        dados["quadra"] = self.quadra_a
        return Lote(imovel=imovel, **dados)

    def test_cadastro_do_lote_na_quadra(self):
        lote = self._lote(self.imovel1)
        lote.full_clean()
        lote.save()
        self.assertEqual(self.quadra_a.lotes.count(), 1)

    def test_quadra_repetida_no_loteamento_e_recusada(self):
        repetida = Quadra(loteamento=self.loteamento, identificacao="A")
        with self.assertRaises(ValidationError):
            repetida.full_clean()

    def test_mesma_quadra_em_outro_loteamento_e_aceita(self):
        outro = criar_loteamento(self.empresa, nome="Recanto Verde")
        Quadra(loteamento=outro, identificacao="A").full_clean()

    def test_numero_de_lote_repetido_na_quadra_e_recusado(self):
        criar_lote(self.imovel1, self.quadra_a)
        with self.assertRaises(ValidationError) as contexto:
            self._lote(self.imovel2).full_clean()
        self.assertIn("Já existe um lote com esse número nesta quadra.", contexto.exception.messages)

    def test_banco_tambem_recusa_numero_repetido(self):
        criar_lote(self.imovel1, self.quadra_a)
        with self.assertRaises(IntegrityError):
            criar_lote(self.imovel2, self.quadra_a)

    def test_mesmo_numero_em_outra_quadra_e_aceito(self):
        criar_lote(self.imovel1, self.quadra_a)
        quadra_b = Quadra.objects.create(loteamento=self.loteamento, identificacao="B")
        lote = self._lote(self.imovel2)
        lote.quadra = quadra_b
        lote.full_clean()

    def test_quadra_de_outra_empresa_e_recusada(self):
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        quadra_de_fora = Quadra.objects.create(
            loteamento=criar_loteamento(outra_empresa), identificacao="A"
        )
        lote = self._lote(self.imovel1)
        lote.quadra = quadra_de_fora
        with self.assertRaises(ValidationError) as contexto:
            lote.full_clean()
        self.assertIn("quadra", contexto.exception.message_dict)

    def test_entrada_maior_que_preco_e_recusada(self):
        with self.assertRaises(ValidationError) as contexto:
            self._lote(self.imovel1, entrada_minima="200000.00").full_clean()
        self.assertIn("entrada_minima", contexto.exception.message_dict)

    def test_lote_nao_entra_em_casa(self):
        casa = criar_imovel(self.empresa, self.corretor, self.proprietario, codigo="CASA")
        with self.assertRaises(ValidationError):
            self._lote(casa).full_clean()

    def test_resumo_por_quadra_conta_disponiveis_e_vendidos(self):
        criar_lote(self.imovel1, self.quadra_a, numero="1")
        criar_lote(self.imovel2, self.quadra_a, numero="2")
        self.assertEqual(
            self.quadra_a.resumo_lotes(), {"total": 2, "disponiveis": 2, "vendidos": 0}
        )

    def test_lote_publica_como_imovel(self):
        endereco = Endereco.objects.create(
            logradouro="Rua 1", cidade=self.loteamento.cidade, latitude="-23.5", longitude="-46.6",
        )
        self.imovel1.endereco = endereco
        self.imovel1.save()
        criar_lote(self.imovel1, self.quadra_a)
        self.imovel1.publicar()
        self.assertTrue(Imovel.objects.get(pk=self.imovel1.pk).publicado)


class PainelLoteamentoTests(TestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.loteamento = criar_loteamento(self.empresa)
        self.quadra_a = Quadra.objects.create(loteamento=self.loteamento, identificacao="A")
        self.imovel = criar_imovel_lote(self.empresa, self.corretor, self.proprietario, "L1")
        self.client.force_login(self.corretor)

    def test_gestor_nao_acessa_loteamentos(self):
        self.client.force_login(criar_gestor(self.empresa))
        response = self.client.get(reverse("imoveis:loteamento_list"))
        self.assertEqual(response.status_code, 403)

    def test_cadastra_loteamento(self):
        response = self.client.post(reverse("imoveis:loteamento_create"), {
            "nome": "Recanto Verde", "cidade": self.loteamento.cidade.pk,
            "incorporadora": "Beta", "asfalto": "on", "agua": "on", "areas_de_lazer": "Praça",
        })
        loteamento = Loteamento.objects.get(nome="Recanto Verde")
        self.assertRedirects(response, reverse("imoveis:loteamento_detail", args=[loteamento.pk]))
        self.assertEqual(loteamento.empresa, self.empresa)
        self.assertTrue(loteamento.asfalto)
        self.assertFalse(loteamento.esgoto)

    def test_nome_repetido_mostra_erro(self):
        response = self.client.post(reverse("imoveis:loteamento_create"), {
            "nome": "Jardim das Flores", "cidade": self.loteamento.cidade.pk, "incorporadora": "X",
        })
        self.assertContains(response, "Já existe um loteamento com esse nome")

    def test_cadastra_quadra_na_tela_do_loteamento(self):
        url = reverse("imoveis:loteamento_detail", args=[self.loteamento.pk])
        response = self.client.post(url, {"identificacao": "B"})
        self.assertRedirects(response, url)
        self.assertTrue(self.loteamento.quadras.filter(identificacao="B").exists())

    def test_quadra_repetida_mostra_erro(self):
        response = self.client.post(
            reverse("imoveis:loteamento_detail", args=[self.loteamento.pk]), {"identificacao": "A"}
        )
        self.assertContains(response, "Já existe uma quadra com essa identificação")

    def test_tela_mostra_disponiveis_e_vendidos_por_quadra(self):
        criar_lote(self.imovel, self.quadra_a)
        Quadra.objects.create(loteamento=self.loteamento, identificacao="B")
        response = self.client.get(reverse("imoveis:loteamento_detail", args=[self.loteamento.pk]))
        resumos = {q.identificacao: r for q, r in response.context["quadras"]}
        self.assertEqual(resumos["A"], {"total": 1, "disponiveis": 1, "vendidos": 0})
        self.assertEqual(resumos["B"], {"total": 0, "disponiveis": 0, "vendidos": 0})
        self.assertContains(response, "Vendidos")

    def test_loteamento_de_outra_empresa_da_404(self):
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        de_fora = criar_loteamento(outra_empresa)
        response = self.client.get(reverse("imoveis:loteamento_detail", args=[de_fora.pk]))
        self.assertEqual(response.status_code, 404)

    def test_atalho_novo_lote_ja_escolhe_a_natureza(self):
        response = self.client.get(reverse("imoveis:imovel_create") + "?natureza=LOTE")
        self.assertEqual(response.context["form"].initial["natureza"], Imovel.Natureza.LOTE)

    def test_tela_do_lote_so_lista_quadras_da_empresa(self):
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        Quadra.objects.create(loteamento=criar_loteamento(outra_empresa), identificacao="Z")
        response = self.client.get(reverse("imoveis:imovel_detail", args=[self.imovel.pk]))
        form = response.context["detalhes_form"]
        self.assertEqual(list(form.fields["quadra"].queryset), [self.quadra_a])

    def test_salva_lote_e_detalhe_mostra_quadra_e_loteamento(self):
        response = self.client.post(
            reverse("imoveis:imovel_atualizar_detalhes", args=[self.imovel.pk]),
            dados_lote(self.quadra_a),
        )
        self.assertRedirects(response, reverse("imoveis:imovel_detail", args=[self.imovel.pk]))
        response = self.client.get(reverse("imoveis:imovel_detail", args=[self.imovel.pk]))
        self.assertContains(response, "Quadra A")
        self.assertContains(response, "Jardim das Flores")
        self.assertContains(response, "Asfalto")
        self.assertContains(response, "até 120x")

    def test_lote_repetido_mostra_mensagem_clara(self):
        outro = criar_imovel_lote(self.empresa, self.corretor, self.proprietario, "L2")
        criar_lote(outro, self.quadra_a)
        response = self.client.post(
            reverse("imoveis:imovel_atualizar_detalhes", args=[self.imovel.pk]),
            dados_lote(self.quadra_a),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Já existe um lote com esse número nesta quadra.")


class ApiLoteamentoTests(APITestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.loteamento = criar_loteamento(self.empresa)
        self.quadra_a = Quadra.objects.create(loteamento=self.loteamento, identificacao="A")
        self.imovel = criar_imovel_lote(self.empresa, self.corretor, self.proprietario, "L1")
        self.outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        self.loteamento_de_fora = criar_loteamento(self.outra_empresa, nome="De fora")
        self.quadra_de_fora = Quadra.objects.create(
            loteamento=self.loteamento_de_fora, identificacao="A"
        )
        self.client.force_authenticate(self.corretor)

    def test_cria_loteamento(self):
        response = self.client.post(reverse("loteamento-list"), {
            "nome": "Recanto Verde", "cidade": self.loteamento.cidade.pk,
            "incorporadora": "Beta", "agua": True,
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(Loteamento.objects.get(pk=response.data["id"]).empresa, self.empresa)

    def test_lista_so_loteamentos_da_empresa(self):
        response = self.client.get(reverse("loteamento-list"))
        self.assertEqual([l["nome"] for l in response.data], ["Jardim das Flores"])

    def test_loteamento_de_outra_empresa_da_404(self):
        response = self.client.get(reverse("loteamento-detail", args=[self.loteamento_de_fora.pk]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_loteamento_traz_resumo_por_quadra(self):
        criar_lote(self.imovel, self.quadra_a)
        response = self.client.get(reverse("loteamento-detail", args=[self.loteamento.pk]))
        self.assertEqual(response.data["quadras"], [
            {"id": self.quadra_a.pk, "identificacao": "A", "total": 1, "disponiveis": 1, "vendidos": 0}
        ])

    def test_cria_quadra(self):
        response = self.client.post(reverse("quadra-list"), {
            "loteamento": self.loteamento.pk, "identificacao": "B",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_quadra_repetida_e_recusada(self):
        response = self.client.post(reverse("quadra-list"), {
            "loteamento": self.loteamento.pk, "identificacao": "A",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_nao_cria_quadra_em_loteamento_de_outra_empresa(self):
        response = self.client.post(reverse("quadra-list"), {
            "loteamento": self.loteamento_de_fora.pk, "identificacao": "B",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("loteamento", response.data)

    def test_quadra_de_outra_empresa_da_404(self):
        response = self.client.get(reverse("quadra-detail", args=[self.quadra_de_fora.pk]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_put_detalhes_do_lote(self):
        response = self.client.put(
            reverse("imovel-detalhes", args=[self.imovel.pk]), dados_lote(self.quadra_a),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["quadra"]["loteamento"]["nome"], "Jardim das Flores")

    def test_detalhe_do_lote_traz_quadra_e_loteamento(self):
        criar_lote(self.imovel, self.quadra_a)
        response = self.client.get(reverse("imovel-detail", args=[self.imovel.pk]))
        lote = response.data["lote"]
        self.assertEqual(response.data["natureza"], "LOTE")
        self.assertEqual(response.data["preco_venda"], "120000.00")
        self.assertEqual(lote["numero"], "7")
        self.assertEqual(lote["parcelas_maximas"], 120)
        self.assertFalse(lote["vendido"])
        self.assertEqual(lote["quadra"]["identificacao"], "A")
        self.assertTrue(lote["quadra"]["loteamento"]["asfalto"])
        self.assertEqual(lote["quadra"]["loteamento"]["incorporadora"], "Incorporadora Alfa")

    def test_lote_repetido_e_recusado_com_mensagem(self):
        outro = criar_imovel_lote(self.empresa, self.corretor, self.proprietario, "L2")
        criar_lote(outro, self.quadra_a)
        response = self.client.put(
            reverse("imovel-detalhes", args=[self.imovel.pk]), dados_lote(self.quadra_a),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            "Já existe um lote com esse número nesta quadra.",
            [str(m) for m in response.data["non_field_errors"]],
        )

    def test_nao_liga_lote_a_quadra_de_outra_empresa(self):
        response = self.client.put(
            reverse("imovel-detalhes", args=[self.imovel.pk]), dados_lote(self.quadra_de_fora),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("quadra", response.data)

    def test_lote_publicado_pela_api(self):
        criar_lote(self.imovel, self.quadra_a)
        self.client.put(reverse("imovel-endereco", args=[self.imovel.pk]), {
            "logradouro": "Rua 1", "cidade": self.loteamento.cidade.pk,
            "latitude": "-23.5", "longitude": "-46.6",
        })
        response = self.client.post(reverse("imovel-publicar", args=[self.imovel.pk]))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertTrue(response.data["publicado"])
        self.assertEqual(response.data["lote"]["quadra"]["loteamento"]["nome"], "Jardim das Flores")

    def test_nao_exclui_quadra_com_lotes(self):
        criar_lote(self.imovel, self.quadra_a)
        response = self.client.delete(reverse("quadra-detail", args=[self.quadra_a.pk]))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
