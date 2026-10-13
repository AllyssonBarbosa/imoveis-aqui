from django.core.exceptions import NON_FIELD_ERRORS, ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers
from rest_framework.settings import api_settings

from core.models import Endereco

from ..models import Apartamento, Caracteristica, Casa, FotoImovel, Imovel, Predio, Terreno


class CaracteristicaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Caracteristica
        fields = ["id", "nome"]


class EnderecoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Endereco
        fields = [
            "id", "cep", "logradouro", "numero", "complemento", "bairro",
            "cidade", "latitude", "longitude",
        ]


def _erros_do_model(erro):
    """Converte o ValidationError do Django pro formato do DRF (__all__ vira non_field_errors)."""
    erros = dict(erro.message_dict)
    if NON_FIELD_ERRORS in erros:
        erros[api_settings.NON_FIELD_ERRORS_KEY] = erros.pop(NON_FIELD_ERRORS)
    return serializers.ValidationError(erros)


class FotoImovelSerializer(serializers.ModelSerializer):
    class Meta:
        model = FotoImovel
        fields = ["id", "imovel", "imagem", "ordem", "capa"]
        read_only_fields = ["id", "imovel", "capa"]


class DetalhesSerializerBase(serializers.ModelSerializer):
    """Valida pelo full_clean do model — a regra fica num lugar só (models.py).

    Espera o imóvel em context["imovel"]; ele não é campo editável.
    """

    def validate(self, attrs):
        instancia = self.instance or self.Meta.model(imovel=self.context["imovel"])
        for campo, valor in attrs.items():
            setattr(instancia, campo, valor)
        try:
            instancia.full_clean(exclude=["imovel"])
        except DjangoValidationError as erro:
            raise _erros_do_model(erro)
        return attrs

    def create(self, validated_data):
        return self.Meta.model.objects.create(imovel=self.context["imovel"], **validated_data)


class PredioSerializer(serializers.ModelSerializer):
    """Prédio com o endereço aninhado — cadastrado uma vez, lido por todas as unidades."""

    endereco = EnderecoSerializer()
    total_apartamentos = serializers.IntegerField(source="apartamentos.count", read_only=True)

    class Meta:
        model = Predio
        fields = [
            "id", "nome", "endereco", "andares", "unidades_por_andar", "ano_construcao",
            "elevadores", "portaria_24h", "piscina", "academia", "salao_de_festas",
            "playground", "valor_condominio_base", "total_apartamentos",
        ]
        # a unicidade (empresa, nome) é checada no full_clean, com a mensagem em português
        validators = []

    def validate(self, attrs):
        instancia = self.instance or Predio()
        for campo, valor in attrs.items():
            if campo != "endereco":
                setattr(instancia, campo, valor)
        instancia.empresa = self.context["request"].user.empresa
        try:
            instancia.full_clean(exclude=["endereco"])
        except DjangoValidationError as erro:
            raise _erros_do_model(erro)
        return attrs

    def create(self, validated_data):
        dados_endereco = validated_data.pop("endereco")
        with transaction.atomic():
            endereco = Endereco.objects.create(**dados_endereco)
            return Predio.objects.create(endereco=endereco, **validated_data)

    def update(self, instance, validated_data):
        dados_endereco = validated_data.pop("endereco", None)
        with transaction.atomic():
            if dados_endereco is not None:
                for campo, valor in dados_endereco.items():
                    setattr(instance.endereco, campo, valor)
                instance.endereco.save()
            for campo, valor in validated_data.items():
                setattr(instance, campo, valor)
            instance.save()
        return instance


class CasaSerializer(DetalhesSerializerBase):
    area_livre = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = Casa
        fields = [
            "area_terreno", "area_construida", "area_livre", "quartos", "suites", "banheiros",
            "salas", "vagas", "pavimentos", "ano_construcao", "piscina", "quintal",
            "churrasqueira", "mobiliada",
        ]


class TerrenoSerializer(DetalhesSerializerBase):
    class Meta:
        model = Terreno
        fields = [
            "area_total", "frente", "fundo", "lateral_esquerda", "lateral_direita",
            "topografia", "murado", "esquina", "uso_permitido",
        ]


class ApartamentoSerializer(DetalhesSerializerBase):
    """Na escrita, `predio` é o id; na leitura, vem o prédio inteiro (endereço, lazer...)."""

    class Meta:
        model = Apartamento
        fields = [
            "predio", "andar", "numero", "area_privativa", "area_total", "quartos", "suites",
            "banheiros", "vagas", "varanda", "posicao_solar",
        ]
        # andar + número repetido no prédio é checado no full_clean, com a mensagem em português
        validators = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        imovel = self.context.get("imovel")
        if imovel is not None:
            # prédio de outra empresa cai em "não existe", sem revelar que existe
            self.fields["predio"].queryset = Predio.objects.filter(empresa=imovel.empresa)

    def to_representation(self, instancia):
        dados = super().to_representation(instancia)
        dados["predio"] = PredioSerializer(instancia.predio).data
        return dados


SERIALIZER_POR_NATUREZA = {
    Imovel.Natureza.CASA: CasaSerializer,
    Imovel.Natureza.TERRENO: TerrenoSerializer,
    Imovel.Natureza.APARTAMENTO: ApartamentoSerializer,
}


class ImovelSerializer(serializers.ModelSerializer):
    # apartamento mostra o endereço do prédio
    endereco = EnderecoSerializer(source="endereco_do_anuncio", read_only=True)
    casa = serializers.SerializerMethodField()
    terreno = serializers.SerializerMethodField()
    apartamento = serializers.SerializerMethodField()
    fotos = FotoImovelSerializer(many=True, read_only=True)
    pode_publicar = serializers.BooleanField(read_only=True)

    class Meta:
        model = Imovel
        fields = [
            "id", "codigo", "titulo", "natureza", "corretor_responsavel", "proprietario", "finalidade",
            "preco_venda", "preco_aluguel", "preco_condominio", "preco_iptu", "descricao",
            "caracteristicas", "casa", "terreno", "apartamento", "endereco", "fotos", "publicado", "pode_publicar", "criado_em",
        ]
        read_only_fields = ["id", "publicado", "endereco", "fotos", "pode_publicar", "criado_em"]

    def _detalhes_se_for(self, imovel, natureza):
        if imovel.natureza != natureza or imovel.detalhes is None:
            return None
        return SERIALIZER_POR_NATUREZA[natureza](imovel.detalhes).data

    def get_casa(self, imovel):
        return self._detalhes_se_for(imovel, Imovel.Natureza.CASA)

    def get_terreno(self, imovel):
        return self._detalhes_se_for(imovel, Imovel.Natureza.TERRENO)

    def get_apartamento(self, imovel):
        return self._detalhes_se_for(imovel, Imovel.Natureza.APARTAMENTO)

    def validate_natureza(self, valor):
        if self.instance and valor != self.instance.natureza:
            raise serializers.ValidationError("A natureza não pode ser trocada depois do cadastro.")
        return valor

    def validate(self, attrs):
        request = self.context["request"]
        instancia = self.instance or Imovel()
        for campo, valor in attrs.items():
            if campo == "caracteristicas":
                continue
            setattr(instancia, campo, valor)
        instancia.empresa = request.user.empresa

        try:
            instancia.full_clean(exclude=["endereco"])
        except DjangoValidationError as erro:
            raise serializers.ValidationError(erro.message_dict)
        return attrs

    def create(self, validated_data):
        caracteristicas = validated_data.pop("caracteristicas", [])
        imovel = Imovel.objects.create(**validated_data)
        imovel.caracteristicas.set(caracteristicas)
        return imovel

    def update(self, instance, validated_data):
        caracteristicas = validated_data.pop("caracteristicas", None)
        for campo, valor in validated_data.items():
            setattr(instance, campo, valor)
        instance.save()
        if caracteristicas is not None:
            instance.caracteristicas.set(caracteristicas)
        return instance
