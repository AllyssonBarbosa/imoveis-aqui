import operator
import re
from decimal import Decimal, InvalidOperation
from functools import reduce

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, F, Prefetch, Q
from django.db.models.functions import Coalesce
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.mixins import EmpresaScopedQuerySetMixin
from core.permissions import EhCorretor, EhGestor
from core.throttles import ThrottleVitrinePublica
from localizacao.models import Cidade

from ..models import (
    Caracteristica,
    DocumentoImovel,
    FotoImovel,
    Imovel,
    Loteamento,
    Predio,
    Quadra,
)
from ..vitrine import imoveis_da_vitrine
from .serializers import (
    SERIALIZER_POR_NATUREZA,
    CaracteristicaSerializer,
    DocumentoImovelSerializer,
    EnderecoSerializer,
    FotoImovelSerializer,
    HistoricoSituacaoSerializer,
    ImovelSerializer,
    ImovelVitrineDetalheSerializer,
    ImovelVitrineSerializer,
    LoteamentoSerializer,
    MudancaSituacaoSerializer,
    PredioSerializer,
    QuadraSerializer,
)

IMOVEL_COM_DETALHES = Imovel.objects.select_related(
    "endereco", "casa", "terreno", "apartamento__predio__endereco", "lote__quadra__loteamento",
).prefetch_related("fotos")


class CaracteristicaViewSet(viewsets.ReadOnlyModelViewSet):
    """Lista de leitura — quem mantém é o administrador, pelo Django Admin."""

    serializer_class = CaracteristicaSerializer
    permission_classes = [EhCorretor]
    queryset = Caracteristica.objects.all()


class ImovelViewSet(EmpresaScopedQuerySetMixin, viewsets.ModelViewSet):
    serializer_class = ImovelSerializer
    permission_classes = [EhCorretor]
    queryset = IMOVEL_COM_DETALHES

    @action(detail=True, methods=["get", "put"])
    def endereco(self, request, pk=None):
        imovel = self.get_object()

        if request.method == "GET":
            if not imovel.endereco_do_anuncio:
                return Response(None)
            return Response(EnderecoSerializer(imovel.endereco_do_anuncio).data)

        if imovel.natureza == Imovel.Natureza.APARTAMENTO:
            return Response(
                {"detail": "O endereço do apartamento é o do prédio — altere pelo cadastro do prédio."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = EnderecoSerializer(instance=imovel.endereco, data=request.data)
        serializer.is_valid(raise_exception=True)
        endereco = serializer.save()
        if not imovel.endereco_id:
            imovel.endereco = endereco
            imovel.save(update_fields=["endereco"])
        return Response(EnderecoSerializer(endereco).data)

    @action(detail=True, methods=["get", "put"])
    def detalhes(self, request, pk=None):
        """Dados próprios da natureza: os campos de casa ou de terreno, conforme o imóvel."""
        imovel = self.get_object()
        serializer_class = SERIALIZER_POR_NATUREZA[imovel.natureza]

        if request.method == "GET":
            if imovel.detalhes is None:
                return Response(None)
            return Response(serializer_class(imovel.detalhes).data)

        serializer = serializer_class(
            instance=imovel.detalhes, data=request.data, context={"imovel": imovel}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    # publicar saiu daqui na W08: publicação e situação são do gestor (GestaoImovelViewSet)


class PredioViewSet(EmpresaScopedQuerySetMixin, viewsets.ModelViewSet):
    """Prédios da própria empresa. Alterar aqui reflete em todos os apartamentos ligados."""

    serializer_class = PredioSerializer
    permission_classes = [EhCorretor]
    queryset = Predio.objects.select_related("endereco")

    def destroy(self, request, *args, **kwargs):
        predio = self.get_object()
        if predio.apartamentos.exists():
            return Response(
                {"detail": "Este prédio tem apartamentos ligados e não pode ser excluído."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        endereco = predio.endereco
        predio.delete()
        endereco.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class LoteamentoViewSet(EmpresaScopedQuerySetMixin, viewsets.ModelViewSet):
    """Loteamentos da própria empresa, com o resumo de lotes por quadra."""

    serializer_class = LoteamentoSerializer
    permission_classes = [EhCorretor]
    queryset = Loteamento.objects.prefetch_related("quadras__lotes__imovel")

    def destroy(self, request, *args, **kwargs):
        loteamento = self.get_object()
        if Quadra.objects.filter(loteamento=loteamento, lotes__isnull=False).exists():
            return Response(
                {"detail": "Este loteamento tem lotes cadastrados e não pode ser excluído."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)


class QuadraViewSet(viewsets.ModelViewSet):
    """Quadras — filtradas por ?loteamento=<id>. Isoladas pela empresa do loteamento."""

    serializer_class = QuadraSerializer
    permission_classes = [EhCorretor]
    queryset = Quadra.objects.prefetch_related("lotes__imovel")

    def get_queryset(self):
        queryset = super().get_queryset().filter(loteamento__empresa=self.request.user.empresa)
        loteamento_id = self.request.query_params.get("loteamento")
        if loteamento_id:
            queryset = queryset.filter(loteamento_id=loteamento_id)
        return queryset

    def destroy(self, request, *args, **kwargs):
        quadra = self.get_object()
        if quadra.lotes.exists():
            return Response(
                {"detail": "Esta quadra tem lotes cadastrados e não pode ser excluída."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)


class FotoImovelViewSet(viewsets.ModelViewSet):
    """Fotos do imóvel — filtradas por ?imovel=<id>. Aceita várias de uma vez."""

    serializer_class = FotoImovelSerializer
    permission_classes = [EhCorretor]
    queryset = FotoImovel.objects.all()

    def get_queryset(self):
        queryset = super().get_queryset().filter(imovel__empresa=self.request.user.empresa)
        imovel_id = self.request.query_params.get("imovel")
        if imovel_id:
            queryset = queryset.filter(imovel_id=imovel_id)
        return queryset

    def create(self, request, *args, **kwargs):
        imovel = get_object_or_404(
            Imovel, pk=request.data.get("imovel"), empresa=request.user.empresa
        )
        arquivos = request.FILES.getlist("imagens") or request.FILES.getlist("imagem")
        if not arquivos:
            return Response({"imagens": ["Envie ao menos uma imagem."]}, status=status.HTTP_400_BAD_REQUEST)

        proxima_ordem = imovel.fotos.count()
        criadas = []
        try:
            for indice, arquivo in enumerate(arquivos):
                foto = FotoImovel(imovel=imovel, imagem=arquivo, ordem=proxima_ordem + indice)
                foto.full_clean()
                foto.save()
                criadas.append(foto)
        except DjangoValidationError as erro:
            return Response({"imagens": erro.messages}, status=status.HTTP_400_BAD_REQUEST)

        serializer = self.get_serializer(criadas, many=True)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def definir_capa(self, request, pk=None):
        foto = self.get_object()
        foto.definir_como_capa()
        return Response(self.get_serializer(foto).data)


class GestaoImovelViewSet(EmpresaScopedQuerySetMixin, viewsets.ReadOnlyModelViewSet):
    """Visão do gestor sobre os imóveis da empresa: publicação, situação e histórico (W08)."""

    serializer_class = ImovelSerializer
    permission_classes = [EhGestor]
    queryset = IMOVEL_COM_DETALHES

    def get_queryset(self):
        queryset = super().get_queryset()
        situacao = self.request.query_params.get("situacao")
        if situacao:
            queryset = queryset.filter(situacao=situacao)
        return queryset

    @action(detail=True, methods=["post"])
    def publicar(self, request, pk=None):
        imovel = self.get_object()
        try:
            imovel.publicar()
        except DjangoValidationError as erro:
            return Response(
                {"detail": erro.messages[0], "pendencias": imovel.pendencias_para_publicar()},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(self.get_serializer(imovel).data)

    @action(detail=True, methods=["post"])
    def despublicar(self, request, pk=None):
        imovel = self.get_object()
        imovel.despublicar()
        return Response(self.get_serializer(imovel).data)

    @action(detail=True, methods=["post"], serializer_class=MudancaSituacaoSerializer)
    def situacao(self, request, pk=None):
        imovel = self.get_object()
        entrada = MudancaSituacaoSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        try:
            imovel.mudar_situacao(
                entrada.validated_data["situacao"], request.user,
                entrada.validated_data["observacao"],
            )
        except DjangoValidationError as erro:
            return Response({"situacao": erro.messages}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ImovelSerializer(imovel, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["get"])
    def historico(self, request, pk=None):
        imovel = self.get_object()
        mudancas = imovel.historico_situacao.select_related("alterado_por")
        return Response(HistoricoSituacaoSerializer(mudancas, many=True).data)


class DocumentoImovelViewSet(
    mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin, viewsets.GenericViewSet,
):
    """Documentos do imóvel, só do gestor — filtrados por ?imovel=<id> (W08)."""

    serializer_class = DocumentoImovelSerializer
    permission_classes = [EhGestor]
    parser_classes = [MultiPartParser, FormParser]
    queryset = DocumentoImovel.objects.select_related("enviado_por")

    def get_queryset(self):
        queryset = super().get_queryset().filter(imovel__empresa=self.request.user.empresa)
        imovel_id = self.request.query_params.get("imovel")
        if imovel_id:
            queryset = queryset.filter(imovel_id=imovel_id)
        return queryset

    def perform_create(self, serializer):
        serializer.save(enviado_por=self.request.user)

    def perform_destroy(self, documento):
        documento.arquivo.delete(save=False)
        documento.delete()

    @action(detail=True, methods=["get"])
    def baixar(self, request, pk=None):
        documento = self.get_object()  # já filtrado pela empresa
        return FileResponse(documento.arquivo.open("rb"), as_attachment=True)


# --- Vitrine pública (W09) ---------------------------------------------------


class PaginacaoVitrine(PageNumberPagination):
    page_size = 20


ORDENACOES_VITRINE = {
    "recentes": [F("criado_em").desc()],
    "preco": [F("preco_anuncio").asc(nulls_last=True)],
    "-preco": [F("preco_anuncio").desc(nulls_last=True)],
    "area": [F("area_anuncio").asc(nulls_last=True)],
    "-area": [F("area_anuncio").desc(nulls_last=True)],
}


# Formato aceito para número na URL: só dígitos ASCII (`[0-9]`, nunca `\d`, que aceita dígito de
# outros alfabetos), sinal opcional e, no decimal, fração com ponto. Fora disso (NaN, Infinity,
# expoente, `_`, espaço) é 400 — `Decimal()` e `int()` aceitariam tudo isso.
_FORMATO_DECIMAL = re.compile(r"-?[0-9]+(\.[0-9]+)?")
_FORMATO_INTEIRO = re.compile(r"-?[0-9]+")

# Maior preço que as colunas de preço guardam (max_digits=12, decimal_places=2).
MAXIMO_FILTRO_PRECO = Decimal("9999999999.99")
# Maior área que as colunas de área guardam (max_digits=10): `area_anuncio` é um Case sobre área
# construída, privativa, total e do lote. Filtrar além disso não muda o resultado, e um backend que
# ajuste o parâmetro ao (10,2) levantaria InvalidOperation (500). Por isso a área não usa o teto do preço.
MAXIMO_FILTRO_AREA = Decimal("99999999.99")
# Teto do PositiveSmallIntegerField (quartos, suítes e vagas).
MAXIMO_FILTRO_INTEIRO = 32767

# Tetos de texto: `busca` e CSV de `bairro` (WR-02). 100 é o max_length de `Endereco.bairro`.
MAXIMO_CARACTERES_BUSCA = 200
MAXIMO_BAIRROS = 20
MAXIMO_CARACTERES_BAIRRO = 100

_MENSAGEM_NUL = "O texto tem um caractere inválido."

# Id de característica na URL: só dígitos ASCII (mesmo critério do `_FORMATO_INTEIRO`; `int()` aceitaria
# `-1`, `1_0` e dígito de outros alfabetos), com no máximo 18 dígitos — cabe no bigint da chave primária.
# O teto vale também para zeros à esquerda: sem ele, 4301+ dígitos estourariam o limite do `int()` do
# Python (ValueError, 500) e um id de 30 dígitos passaria do range do inteiro ligado à consulta (WR-07).
_FORMATO_ID = re.compile(r"[0-9]{1,18}")
# Maior que o catálogo semeado e que qualquer combinação útil de "tem todas". Cada id custava um JOIN
# na tabela M2M (CR-01); o teto barra a requisição antes de qualquer consulta.
MAXIMO_CARACTERISTICAS = 20


def _numero(params, nome, maximo, tipo=Decimal):
    """Lê um filtro numérico da URL — valor inválido vira 400 com mensagem clara.

    Ordem: vazio é ignorado; o texto cru precisa casar com o formato permitido (allowlist ASCII);
    converte; decimal precisa ser finito; depois negativo e teto (`maximo`, o da coluna filtrada).
    """
    valor = params.get(nome)
    if valor in (None, ""):
        return None
    formato = _FORMATO_DECIMAL if tipo is Decimal else _FORMATO_INTEIRO
    if not formato.fullmatch(valor):
        raise ValidationError({nome: "Informe um número válido."})
    try:
        numero = tipo(valor)
    except (InvalidOperation, ValueError):
        raise ValidationError({nome: "Informe um número válido."})
    if tipo is Decimal and not numero.is_finite():
        raise ValidationError({nome: "Informe um número válido."})
    if numero < 0:
        raise ValidationError({nome: "O valor não pode ser negativo."})
    if numero > maximo:
        raise ValidationError({nome: "O valor passa do máximo permitido."})
    return numero


def _texto(params, nome, maximo):
    """Lê um texto livre da URL: sem byte NUL, aparado e com no máximo `maximo` caracteres."""
    valor = params.get(nome, "")
    # o Postgres não guarda NUL em texto: o driver levantaria DataError (500). Recusa em Python,
    # antes da consulta, para o 400 valer em qualquer banco.
    if "\x00" in valor:
        raise ValidationError({nome: _MENSAGEM_NUL})
    valor = valor.strip()
    if len(valor) > maximo:
        raise ValidationError({nome: f"Use no máximo {maximo} caracteres."})
    return valor


def _bairros_do_filtro(params):
    """Bairros do `?bairro=Moema,Pinheiros`: itens aparados, vazios descartados, com tetos."""
    valor = params.get("bairro", "")
    if "\x00" in valor:
        raise ValidationError({"bairro": _MENSAGEM_NUL})
    bairros = [item.strip() for item in valor.split(",") if item.strip()]
    if len(bairros) > MAXIMO_BAIRROS:
        raise ValidationError({"bairro": f"Escolha no máximo {MAXIMO_BAIRROS} bairros."})
    if any(len(bairro) > MAXIMO_CARACTERES_BAIRRO for bairro in bairros):
        raise ValidationError(
            {"bairro": f"Cada bairro tem no máximo {MAXIMO_CARACTERES_BAIRRO} caracteres."}
        )
    return bairros


def _ids_de_caracteristicas(params):
    """Ids do `?caracteristicas=1,3`: itens aparados, vazios descartados, repetidos contam uma vez.

    Cada item precisa ser inteiro ASCII positivo; passou de `MAXIMO_CARACTERISTICAS` ids distintos é 400.
    """
    valor = params.get("caracteristicas", "").strip()
    if not valor:
        return set()
    ids = set()
    for item in valor.split(","):
        item = item.strip()
        if not item:
            continue
        if not _FORMATO_ID.fullmatch(item):
            raise ValidationError({"caracteristicas": "Informe os ids separados por vírgula."})
        numero = int(item)  # seguro: até 18 dígitos ASCII
        if numero <= 0:
            raise ValidationError({"caracteristicas": "Informe os ids separados por vírgula."})
        ids.add(numero)
    if len(ids) > MAXIMO_CARACTERISTICAS:
        raise ValidationError(
            {"caracteristicas": f"Escolha no máximo {MAXIMO_CARACTERISTICAS} características."}
        )
    return ids


def _escolha(params, nome, opcoes):
    valor = params.get(nome)
    if valor in (None, ""):
        return None
    if valor not in opcoes:
        raise ValidationError({nome: f"Opções válidas: {', '.join(opcoes)}."})
    return valor


def _escolhas(params, nome, opcoes):
    """Vários valores separados por vírgula (OU). Itens vazios e espaços são descartados;
    qualquer item fora de `opcoes` vira 400.
    """
    itens = [item.strip() for item in params.get(nome, "").split(",") if item.strip()]
    for item in itens:
        if item not in opcoes:
            raise ValidationError({nome: f"Opções válidas: {', '.join(opcoes)}."})
    return itens


class VitrineCidadeAPIView(generics.ListAPIView):
    """Busca da vitrine, sempre dentro de uma cidade. Pública: sem login e sem token.

    authentication_classes vazio: nem um token vencido no app impede a consulta.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ThrottleVitrinePublica]
    serializer_class = ImovelVitrineSerializer
    pagination_class = PaginacaoVitrine

    def get_queryset(self):
        cidade = get_object_or_404(Cidade, pk=self.kwargs["cidade_id"])
        params = self.request.query_params
        queryset = imoveis_da_vitrine().filter(cidade_anuncio=cidade.pk)

        busca = _texto(params, "busca", MAXIMO_CARACTERES_BUSCA)
        if busca:
            queryset = queryset.filter(
                Q(titulo__icontains=busca)
                | Q(bairro_anuncio__icontains=busca)
                | Q(descricao__icontains=busca)
            )

        # "venda" também traz quem é "venda e aluguel" (e o mesmo pro aluguel)
        finalidade = _escolha(params, "finalidade", Imovel.Finalidade.values)
        if finalidade:
            queryset = queryset.filter(
                finalidade__in=[finalidade, Imovel.Finalidade.VENDA_E_ALUGUEL]
            )

        naturezas = _escolhas(params, "natureza", Imovel.Natureza.values)
        if naturezas:
            queryset = queryset.filter(natureza__in=naturezas)

        # o preço comparado depende da finalidade buscada; sem finalidade, vale o que houver
        if finalidade == Imovel.Finalidade.VENDA:
            preco = F("preco_venda")
        elif finalidade == Imovel.Finalidade.ALUGUEL:
            preco = F("preco_aluguel")
        else:
            preco = Coalesce("preco_venda", "preco_aluguel")
        queryset = queryset.annotate(preco_anuncio=preco)

        # cada faixa tem o teto da própria coluna: preço (12 dígitos) e área (10 dígitos) diferem
        faixas = {
            "preco_min": ("preco_anuncio__gte", MAXIMO_FILTRO_PRECO),
            "preco_max": ("preco_anuncio__lte", MAXIMO_FILTRO_PRECO),
            "area_min": ("area_anuncio__gte", MAXIMO_FILTRO_AREA),
            "area_max": ("area_anuncio__lte", MAXIMO_FILTRO_AREA),
        }
        for nome, (lookup, maximo) in faixas.items():
            valor = _numero(params, nome, maximo)
            if valor is not None:
                queryset = queryset.filter(**{lookup: valor})

        # quartos/suítes/vagas são "no mínimo" — quem pede 2 quartos aceita 3
        minimos = {"quartos": "quartos_anuncio__gte", "suites": "suites_anuncio__gte", "vagas": "vagas_anuncio__gte"}
        for nome, lookup in minimos.items():
            valor = _numero(params, nome, MAXIMO_FILTRO_INTEIRO, tipo=int)
            if valor is not None:
                queryset = queryset.filter(**{lookup: valor})

        # ?bairro=Moema,Pinheiros — OU entre os bairros, sem diferenciar maiúsculas (por isso o
        # OR de iexact e não __in). Bairro com vírgula no nome não cabe no CSV (risco aceito, A-05).
        bairros = _bairros_do_filtro(params)
        if bairros:
            queryset = queryset.filter(
                reduce(operator.or_, (Q(bairro_anuncio__iexact=bairro) for bairro in bairros))
            )

        # ?caracteristicas=1,3 — o imóvel precisa ter todas as escolhidas
        ids = _ids_de_caracteristicas(params)
        if ids:
            # o imóvel entra se tiver todas as escolhidas: uma só subconsulta conta quantas delas ele
            # tem (GROUP BY imóvel HAVING COUNT DISTINCT = quantidade pedida), sem um JOIN por id (CR-01)
            com_todas = (
                Imovel.caracteristicas.through.objects.filter(caracteristica_id__in=ids)
                .values("imovel_id")
                .annotate(total=Count("caracteristica_id", distinct=True))
                .filter(total=len(ids))
                .values("imovel_id")
            )
            queryset = queryset.filter(pk__in=com_todas)

        ordenar = _escolha(params, "ordenar", list(ORDENACOES_VITRINE)) or "recentes"
        # o id no fim deixa a ordem estável entre as páginas quando há empate
        return (
            queryset.order_by(*ORDENACOES_VITRINE[ordenar], "-id")
            .distinct()
            .prefetch_related("fotos")
        )


class BairrosDaCidadeAPIView(APIView):
    """Opções de bairro da vitrine — sem login; derivadas da mesma imoveis_da_vitrine().

    Como saem da mesma regra da lista, escolher uma opção nunca leva a uma busca vazia.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ThrottleVitrinePublica]

    def get(self, request, cidade_id):
        cidade = get_object_or_404(Cidade, pk=cidade_id)
        bairros = (
            imoveis_da_vitrine()
            .filter(cidade_anuncio=cidade.pk)
            .exclude(bairro_anuncio__isnull=True)
            .exclude(bairro_anuncio="")
            .order_by("bairro_anuncio")
            .values_list("bairro_anuncio", flat=True)
            .distinct()
        )
        return Response(list(bairros))


class CaracteristicasPublicasAPIView(generics.ListAPIView):
    """Catálogo global de características (id e nome), sem login — alimenta o filtro do app.

    Sem paginação: o catálogo é curto e o app precisa dele inteiro para montar as opções.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ThrottleVitrinePublica]
    pagination_class = None
    serializer_class = CaracteristicaSerializer
    queryset = Caracteristica.objects.all()  # Meta.ordering = ["nome"]


class ImovelVitrineDetalheAPIView(generics.RetrieveAPIView):
    """Página pública do imóvel. Rascunho ou empresa inativa cai em 404."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ThrottleVitrinePublica]
    serializer_class = ImovelVitrineDetalheSerializer

    def get_queryset(self):
        return imoveis_da_vitrine().select_related(
            "endereco__cidade", "corretor_responsavel__empresa", "casa", "terreno",
            "apartamento__predio__endereco__cidade", "lote__quadra__loteamento",
        ).prefetch_related(
            # a capa abre a galeria (regra da API; o app não reordena) — depois ordem e id
            Prefetch("fotos", queryset=FotoImovel.objects.order_by("-capa", "ordem", "id")),
            "caracteristicas",
        )
