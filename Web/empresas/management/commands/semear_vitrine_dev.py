import io
from decimal import Decimal

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from PIL import Image

from contas.models import Usuario
from core.models import Endereco
from empresas.models import Empresa
from imoveis.models import (
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
from localizacao.models import Cidade
from proprietarios.models import Proprietario

# Mesmas 4 cidades de contrato/cidades.example.json e da fixture mock do app
# (RESEARCH Pitfall 4 / A3) — não divergir sem atualizar as duas fontes juntas.
CIDADES_VITRINE = [
    ("Campinas", "SP"),
    ("Valinhos", "SP"),
    ("Vinhedo", "SP"),
    ("Indaiatuba", "SP"),
]

NOME_EMPRESA_DEMO = "Imobiliária Vitrine Demo (dev)"

EMAIL_CORRETOR_DEMO = "corretor.vitrine.demo@example.com"
EMAIL_CORRETOR_INATIVO_DEMO = "corretor.inativo.vitrine.demo@example.com"
DOCUMENTO_PROPRIETARIO_DEMO = "17662633055"  # CPF válido (dígitos verificadores corretos)

CARACTERISTICAS_DEMO = [
    "Piscina",
    "Churrasqueira",
    "Portão eletrônico",
    "Ar-condicionado",
    "Varanda gourmet",
    "Quintal",
]

# Quantos imóveis cada cidade recebe. Natureza vai em ordem intercalada
# (casa, apartamento, terreno, lote, casa, ...) — assim, mesmo na ordem "recentes",
# as quatro naturezas aparecem juntas na primeira página.
# Campinas tem 24 (mais de uma página de 20); as outras cidades, um de cada natureza.
IMOVEIS_POR_CIDADE = {"Campinas": 24, "Valinhos": 4, "Vinhedo": 4, "Indaiatuba": 4}

# Casos de borda que o app consome, todos entre os 20 mais recentes de Campinas
# (índice base 0 => códigos DEV-CAMPINAS-11, -14 e -17): um imóvel sem nenhuma foto,
# um cujo corretor responsável foi desativado e um cuja capa é a foto de ordem 2.
INDICE_SEM_FOTO = 10
INDICE_CORRETOR_INATIVO = 13
INDICE_CAPA_NA_TERCEIRA_FOTO = 16

NATUREZAS_EM_ORDEM = [
    Imovel.Natureza.CASA,
    Imovel.Natureza.APARTAMENTO,
    Imovel.Natureza.TERRENO,
    Imovel.Natureza.LOTE,
]

BAIRROS_POR_CIDADE = {
    "Campinas": ["Cambuí", "Centro", "Taquaral", "Barão Geraldo", "Nova Campinas"],
    "Valinhos": ["Centro", "Jardim Primavera"],
    "Vinhedo": ["Centro", "Santa Claudina"],
    "Indaiatuba": ["Centro", "Jardim Esplanada"],
}

# (latitude, longitude) do centro de cada cidade; cada imóvel recebe um pequeno desvio.
COORDENADAS_POR_CIDADE = {
    "Campinas": (Decimal("-22.905600"), Decimal("-47.060800")),
    "Valinhos": (Decimal("-22.970800"), Decimal("-46.997200")),
    "Vinhedo": (Decimal("-23.030000"), Decimal("-46.975300")),
    "Indaiatuba": (Decimal("-23.090300"), Decimal("-47.218100")),
}

LOGRADOUROS = [
    "Rua das Acácias", "Avenida Brasil", "Rua dos Ipês", "Rua das Palmeiras",
    "Avenida Norte-Sul", "Rua Barão de Itapura",
]

CORES_DAS_FOTOS = ["seagreen", "darkseagreen", "forestgreen", "mediumseagreen", "olivedrab", "teal"]

FINALIDADES_EM_ORDEM = [
    Imovel.Finalidade.VENDA,
    Imovel.Finalidade.ALUGUEL,
    Imovel.Finalidade.VENDA_E_ALUGUEL,
    Imovel.Finalidade.VENDA,
]


def _gerar_jpeg(cor):
    """Bytes de um JPEG 800x600 de cor única, gerado pelo Pillow (nenhuma imagem versionada)."""
    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), color=cor).save(buffer, format="JPEG")
    return buffer.getvalue()


def _arquivo_jpeg(nome, cor):
    return SimpleUploadedFile(nome, _gerar_jpeg(cor), content_type="image/jpeg")


def _codigo(nome_cidade, numero):
    return f"DEV-{nome_cidade.upper()}-{numero:02d}"


def _decimal(valor):
    return Decimal(str(valor)).quantize(Decimal("0.01"))


class Command(BaseCommand):
    """Semeia o banco de desenvolvimento com um acervo real para a vitrine pública (D-09).

    Idempotente (rodar duas vezes não duplica nada) e nunca roda fora de
    DEBUG=True. Busca a Empresa de demonstração só pelo seu nome marcador
    próprio (NOME_EMPRESA_DEMO) — nunca olha nem altera nenhuma outra Empresa.
    Cada objeto é procurado pela sua chave natural antes de ser criado, então uma
    execução interrompida é retomada rodando de novo. Nenhum usuário recebe senha.
    """

    help = (
        "Cria (idempotente) as 4 cidades atendidas da vitrine, uma Empresa de "
        "demonstração ativa e imóveis das 4 naturezas com fotos. Só roda com DEBUG=True."
    )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                "semear_vitrine_dev só pode rodar com DEBUG=True "
                "(ambiente de desenvolvimento)."
            )

        cidades = {}
        for nome, uf in CIDADES_VITRINE:
            cidade, criada = Cidade.objects.get_or_create(nome=nome, uf=uf)
            cidades[nome] = cidade
            if criada:
                self.stdout.write(f"Cidade criada: {cidade}")

        empresa, criada = Empresa.objects.get_or_create(
            razao_social_ou_nome=NOME_EMPRESA_DEMO,
            defaults={
                "tipo": Empresa.Tipo.IMOBILIARIA,
                "cnpj": "66777888000505",
                "creci": "00000-D",
                "whatsapp": "11900000000",
                "email": "vitrine-demo@example.com",
                "ativa": True,
            },
        )
        if criada:
            self.stdout.write(f"Empresa de demonstração criada: {empresa}")
        elif not empresa.ativa:
            empresa.ativa = True
            empresa.save(update_fields=["ativa"])

        empresa.cidades_atuacao.add(*cidades.values())

        corretor = self._garantir_corretor(empresa, EMAIL_CORRETOR_DEMO, "Corretor Vitrine Demo")
        corretor_inativo = self._garantir_corretor(
            empresa, EMAIL_CORRETOR_INATIVO_DEMO, "Corretor Desativado Demo", ativo=False
        )
        proprietario = self._garantir_proprietario(empresa)
        caracteristicas = [
            Caracteristica.objects.get_or_create(nome=nome)[0] for nome in CARACTERISTICAS_DEMO
        ]

        total = 0
        for nome_cidade, quantidade in IMOVEIS_POR_CIDADE.items():
            cidade = cidades[nome_cidade]
            for indice in range(quantidade):
                self._garantir_imovel(
                    empresa=empresa, cidade=cidade, indice=indice, proprietario=proprietario,
                    caracteristicas=caracteristicas, corretor=corretor,
                    corretor_inativo=corretor_inativo,
                )
                total += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Vitrine de desenvolvimento semeada: {len(cidades)} cidades atendidas por "
                f"'{empresa.razao_social_ou_nome}', {total} imóveis garantidos."
            )
        )

    # ------------------------------------------------------------------ usuários e donos

    def _garantir_corretor(self, empresa, email, nome, ativo=True):
        """Corretor da empresa demo, buscado pelo e-mail. Senha sempre inutilizável."""
        corretor = Usuario.objects.filter(email=email).first()
        if corretor is not None:
            if corretor.empresa_id != empresa.pk:
                raise CommandError(
                    f"O e-mail {email} já pertence a outra empresa — o seed não mexe nela."
                )
            return corretor

        corretor = Usuario(
            email=email, nome=nome, perfil=Usuario.Perfil.CORRETOR, empresa=empresa,
            creci="12345-F", telefone="19988887777", whatsapp="19988887777",
            apresentacao="Corretor de demonstração da vitrine de desenvolvimento.",
            is_active=ativo,
        )
        corretor.set_unusable_password()
        corretor.full_clean(exclude=["password"])
        corretor.save()
        corretor.foto.save(
            f"{email.split('@')[0]}.jpg", _arquivo_jpeg("corretor.jpg", "darkgreen"), save=True
        )
        return corretor

    def _garantir_proprietario(self, empresa):
        proprietario, _ = Proprietario.objects.get_or_create(
            empresa=empresa, documento=DOCUMENTO_PROPRIETARIO_DEMO,
            defaults={
                "tipo_pessoa": Proprietario.TipoPessoa.FISICA,
                "nome_razao_social": "Proprietário Demo da Vitrine",
            },
        )
        return proprietario

    # ------------------------------------------------------------------ imóveis

    def _endereco(self, cidade, indice, bairro):
        base_lat, base_lng = COORDENADAS_POR_CIDADE[cidade.nome]
        desvio = Decimal(indice) * Decimal("0.001300")
        return Endereco.objects.create(
            cep=f"130{10 + indice % 80:02d}-000",
            logradouro=LOGRADOUROS[indice % len(LOGRADOUROS)],
            numero=str(100 + indice * 7),
            complemento="" if indice % 2 else "Próximo ao centro",
            bairro=bairro, cidade=cidade,
            latitude=base_lat + desvio, longitude=base_lng - desvio,
        )

    def _garantir_predio(self, empresa, cidade, ordem):
        """Prédio buscado por (empresa, nome); só ele cria o próprio endereço."""
        bairros = BAIRROS_POR_CIDADE[cidade.nome]
        nome = f"Edifício {['Aurora', 'Girassol'][ordem % 2]} {cidade.nome}"
        predio = Predio.objects.filter(empresa=empresa, nome=nome).first()
        if predio is None:
            predio = Predio.objects.create(
                empresa=empresa, nome=nome,
                endereco=self._endereco(cidade, 50 + ordem, bairros[(ordem + 1) % len(bairros)]),
                andares=12, unidades_por_andar=4, ano_construcao=2012 + ordem, elevadores=2,
                portaria_24h=True, piscina=bool(ordem % 2), academia=True,
                salao_de_festas=True, playground=bool(ordem % 2 == 0),
                valor_condominio_base=Decimal("750.00") + 90 * ordem,
            )
        return predio

    def _garantir_quadra(self, empresa, cidade, ordem):
        """Loteamento por (empresa, nome) e quadra por (loteamento, identificação)."""
        loteamento, _ = Loteamento.objects.get_or_create(
            empresa=empresa, nome=f"Loteamento Jardim dos Ipês {cidade.nome}",
            defaults={
                # a mesma cidade do endereço do imóvel: a vitrine usa o endereço, o seed não diverge
                "cidade": cidade,
                "incorporadora": "Incorporadora Verde Vale",
                "asfalto": True, "agua": True, "energia": True, "esgoto": ordem % 2 == 0,
                "iluminacao": True,
                "areas_de_lazer": "Praça central, quadra poliesportiva e pista de caminhada.",
            },
        )
        quadra, _ = Quadra.objects.get_or_create(
            loteamento=loteamento, identificacao="AB"[ordem % 2]
        )
        return quadra

    def _garantir_imovel(
        self, *, empresa, cidade, indice, corretor, corretor_inativo, proprietario, caracteristicas
    ):
        """Imóvel buscado por (empresa, código); tudo dele nasce numa transação só.

        Imóvel que já existe fica como está (inclusive situação e fotos): o seed não
        sobrescreve o que alguém mexeu depois.
        """
        codigo = _codigo(cidade.nome, indice + 1)
        if Imovel.objects.filter(empresa=empresa, codigo=codigo).exists():
            return

        natureza = NATUREZAS_EM_ORDEM[indice % 4]
        rodada = indice // 4  # 0 para o primeiro de cada natureza, 1 para o segundo...
        bairros = BAIRROS_POR_CIDADE[cidade.nome]
        bairro = bairros[indice % len(bairros)]
        finalidade = FINALIDADES_EM_ORDEM[indice % len(FINALIDADES_EM_ORDEM)]
        if natureza == Imovel.Natureza.LOTE:
            finalidade = Imovel.Finalidade.VENDA  # parcelamento só vale para lote à venda

        base_venda = {"CASA": 350000, "APARTAMENTO": 280000, "TERRENO": 150000, "LOTE": 90000}
        base_aluguel = {"CASA": 2500, "APARTAMENTO": 1800, "TERRENO": 1200}
        preco_venda = preco_aluguel = None
        if finalidade != Imovel.Finalidade.ALUGUEL:
            preco_venda = _decimal(base_venda[natureza] + 27000 * rodada + 5000 * (indice % 3))
        if finalidade != Imovel.Finalidade.VENDA:
            preco_aluguel = _decimal(base_aluguel[natureza] + 250 * rodada)

        rotulo = Imovel.Natureza(natureza).label
        em_campinas = cidade.nome == "Campinas"
        if em_campinas and indice == INDICE_CORRETOR_INATIVO:
            corretor = corretor_inativo
        with transaction.atomic():
            endereco = None
            if natureza != Imovel.Natureza.APARTAMENTO:  # apartamento usa o endereço do prédio
                endereco = self._endereco(cidade, indice, bairro)
            imovel = Imovel.objects.create(
                empresa=empresa, codigo=codigo, titulo=f"{rotulo} no bairro {bairro}",
                natureza=natureza, corretor_responsavel=corretor, proprietario=proprietario,
                finalidade=finalidade, preco_venda=preco_venda, preco_aluguel=preco_aluguel,
                descricao=(
                    f"{rotulo} de demonstração em {cidade.nome} ({bairro}), semeado para o "
                    "desenvolvimento da vitrine."
                ),
                endereco=endereco,
            )
            self._criar_bloco_da_natureza(imovel, empresa, cidade, indice, rodada)
            imovel.caracteristicas.set(
                [c for posicao, c in enumerate(caracteristicas) if (indice + posicao) % 3 == 0]
            )
            self._garantir_fotos(imovel, indice, em_campinas)
            imovel.publicar()  # respeita pendencias_para_publicar (endereço, coordenadas, prédio)

    def _criar_bloco_da_natureza(self, imovel, empresa, cidade, indice, rodada):
        natureza = imovel.natureza
        if natureza == Imovel.Natureza.CASA:
            quartos = 2 + indice % 3
            Casa.objects.create(
                imovel=imovel, area_terreno=_decimal(200 + 25 * rodada),
                area_construida=_decimal(90 + 12 * rodada), quartos=quartos,
                suites=min(indice % 3, quartos), banheiros=1 + indice % 3, salas=1 + rodada % 2,
                vagas=1 + indice % 3, pavimentos=1 + rodada % 2, ano_construcao=2005 + rodada,
                piscina=indice % 2 == 0, quintal=True, churrasqueira=indice % 3 == 0,
                mobiliada=indice % 5 == 0,
            )
        elif natureza == Imovel.Natureza.APARTAMENTO:
            predio = self._garantir_predio(empresa, cidade, rodada)
            quartos = 1 + indice % 3
            andar = 1 + indice % 11
            Apartamento.objects.create(
                imovel=imovel, predio=predio, andar=andar, numero=f"{andar}{indice + 1:02d}",
                area_privativa=_decimal(55 + 6 * rodada), area_total=_decimal(70 + 6 * rodada),
                quartos=quartos, suites=min(indice % 2, quartos), banheiros=1 + indice % 2,
                vagas=1 + indice % 2, varanda=indice % 2 == 0,
                posicao_solar=list(Apartamento.PosicaoSolar.values)[indice % 4],
            )
        elif natureza == Imovel.Natureza.TERRENO:
            frente = Decimal(10 + rodada)
            Terreno.objects.create(
                imovel=imovel, area_total=_decimal(frente * 25), frente=_decimal(frente),
                fundo=_decimal(25), lateral_esquerda=_decimal(25), lateral_direita=_decimal(25),
                topografia=list(Terreno.Topografia.values)[indice % 3],
                murado=indice % 2 == 0, esquina=indice % 5 == 0,
                uso_permitido=list(Terreno.UsoPermitido.values)[indice % 3],
            )
        else:
            quadra = self._garantir_quadra(empresa, cidade, rodada)
            frente = Decimal(10 + rodada % 4)
            Lote.objects.create(
                imovel=imovel, quadra=quadra, numero=str(indice + 1),
                area=_decimal(frente * 25), frente=_decimal(frente), fundo=_decimal(25),
                entrada_minima=_decimal(imovel.preco_venda * Decimal("0.10")),
                parcelas_maximas=60 + 12 * (rodada % 6),
                condicoes_parcelamento="Correção anual pelo IPCA.",
            )

    def _garantir_fotos(self, imovel, indice, em_campinas):
        """Fotos JPEG geradas pelo Pillow, só para imóvel que ainda não tem foto.

        Um imóvel de Campinas fica propositalmente sem foto (INDICE_SEM_FOTO) e outro tem a
        capa na foto de ordem 2: a galeria do app precisa dos dois casos.
        """
        if imovel.fotos.exists() or (em_campinas and indice == INDICE_SEM_FOTO):
            return
        quantidade = 3 if indice % 2 == 0 else 2
        posicao_da_capa = 2 if (em_campinas and indice == INDICE_CAPA_NA_TERCEIRA_FOTO) else 0
        for posicao in range(quantidade):
            cor = CORES_DAS_FOTOS[(indice + posicao) % len(CORES_DAS_FOTOS)]
            FotoImovel.objects.create(
                imovel=imovel, ordem=posicao, capa=posicao == posicao_da_capa,
                imagem=_arquivo_jpeg(f"{imovel.codigo.lower()}-{posicao + 1}.jpg", cor),
            )
