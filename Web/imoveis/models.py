from django.core.exceptions import ValidationError
from django.db import models

from core.models import EmpresaOwnedModel
from core.validators import validar_imagem


class Caracteristica(models.Model):
    """Tabela geral mantida pelo administrador (portão eletrônico, ar-condicionado etc.).

    Marcável em cada imóvel — permite filtrar a vitrine por característica
    em vez de vasculhar o texto da descrição.
    """

    nome = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name = "característica"
        verbose_name_plural = "características"
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class Imovel(EmpresaOwnedModel):
    """Parte comum do cadastro do imóvel — a natureza específica vem depois.

    Nasce como rascunho: dá pra cadastrar sem endereço completo e ir
    montando aos poucos (endereço, fotos). Só pode ser publicado quando
    pode_publicar() for verdadeiro (ver W04, critério 6).
    """

    class Finalidade(models.TextChoices):
        VENDA = "VENDA", "Venda"
        ALUGUEL = "ALUGUEL", "Aluguel"
        VENDA_E_ALUGUEL = "VENDA_E_ALUGUEL", "Venda e aluguel"

    codigo = models.CharField(max_length=30)
    titulo = models.CharField(max_length=200)
    corretor_responsavel = models.ForeignKey(
        "contas.Usuario", on_delete=models.PROTECT, related_name="imoveis_responsavel"
    )
    proprietario = models.ForeignKey(
        "proprietarios.Proprietario", on_delete=models.PROTECT, related_name="imoveis"
    )
    finalidade = models.CharField(max_length=20, choices=Finalidade.choices)
    preco_venda = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    preco_aluguel = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    preco_condominio = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    preco_iptu = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    descricao = models.TextField(blank=True)
    caracteristicas = models.ManyToManyField(Caracteristica, blank=True, related_name="imoveis")
    endereco = models.OneToOneField(
        "core.Endereco", on_delete=models.PROTECT, null=True, blank=True, related_name="imovel"
    )
    publicado = models.BooleanField(default=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "imóvel"
        verbose_name_plural = "imóveis"
        ordering = ["-criado_em"]
        constraints = [
            models.UniqueConstraint(
                fields=["empresa", "codigo"],
                name="codigo_unico_por_empresa",
                violation_error_message="Já existe um imóvel com esse código nesta empresa.",
            )
        ]

    def __str__(self):
        return f"{self.codigo} — {self.titulo}"

    def clean(self):
        super().clean()

        # self.empresa_id só está preenchido depois que a view define a empresa
        # (o formulário não tem esse campo) — sem empresa ainda, não dá pra
        # comparar, então esses dois cruzamentos só rodam quando ela existe.
        if self.corretor_responsavel_id:
            from contas.models import Usuario

            if self.empresa_id and self.corretor_responsavel.empresa_id != self.empresa_id:
                raise ValidationError(
                    {"corretor_responsavel": "O corretor responsável precisa ser da mesma empresa."}
                )
            if self.corretor_responsavel.perfil != Usuario.Perfil.CORRETOR:
                raise ValidationError(
                    {"corretor_responsavel": "O responsável precisa ter perfil de corretor."}
                )

        if self.proprietario_id and self.empresa_id and self.proprietario.empresa_id != self.empresa_id:
            raise ValidationError({"proprietario": "O proprietário precisa ser da mesma empresa."})

        precisa_de_venda = self.finalidade in (self.Finalidade.VENDA, self.Finalidade.VENDA_E_ALUGUEL)
        precisa_de_aluguel = self.finalidade in (self.Finalidade.ALUGUEL, self.Finalidade.VENDA_E_ALUGUEL)

        if precisa_de_venda and not self.preco_venda:
            raise ValidationError({"preco_venda": "Finalidade de venda exige o preço de venda."})
        if precisa_de_aluguel and not self.preco_aluguel:
            raise ValidationError({"preco_aluguel": "Finalidade de aluguel exige o valor do aluguel."})

    def pode_publicar(self):
        return bool(
            self.endereco_id
            and self.endereco.latitude is not None
            and self.endereco.longitude is not None
        )

    def publicar(self):
        if not self.pode_publicar():
            raise ValidationError(
                "Imóvel sem cidade ou sem coordenada não pode ser publicado."
            )
        self.publicado = True
        self.save(update_fields=["publicado"])


class FotoImovel(models.Model):
    """Foto do imóvel, com ordem de exibição e uma marcação de capa."""

    imovel = models.ForeignKey(Imovel, on_delete=models.CASCADE, related_name="fotos")
    imagem = models.ImageField(upload_to="imoveis/fotos/", validators=[validar_imagem])
    ordem = models.PositiveIntegerField(default=0)
    capa = models.BooleanField(default=False)

    class Meta:
        verbose_name = "foto do imóvel"
        verbose_name_plural = "fotos do imóvel"
        ordering = ["ordem", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["imovel"],
                condition=models.Q(capa=True),
                name="uma_capa_por_imovel",
                violation_error_message="Este imóvel já tem uma foto de capa.",
            )
        ]

    def __str__(self):
        return f"Foto {self.ordem} de {self.imovel}"

    def definir_como_capa(self):
        FotoImovel.objects.filter(imovel_id=self.imovel_id, capa=True).exclude(pk=self.pk).update(
            capa=False
        )
        self.capa = True
        self.save(update_fields=["capa"])
