from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import EmpresaOwnedModel


class Contato(EmpresaOwnedModel):
    """Registro de que alguém procurou por um imóvel (W11).

    Guarda o contato, não a conversa: a conversa acontece fora do sistema
    (WhatsApp, ligação, e-mail). De propósito, não há nenhum dado do
    visitante aqui — nem nome, nem telefone, nem campo de texto livre.

    `corretor` é quem recebeu o contato no momento do clique. Fica vazio
    quando o corretor responsável estava desativado e o contato foi para
    a empresa (mesma regra do botão da W10) — aí só o gestor enxerga.
    """

    class Canal(models.TextChoices):
        WHATSAPP = "WHATSAPP", "WhatsApp"
        TELEFONE = "TELEFONE", "Telefone"
        EMAIL = "EMAIL", "E-mail"

    class Retorno(models.TextChoices):
        PENDENTE = "PENDENTE", "Aguardando retorno"
        FALEI = "FALEI", "Falei"
        VISITA_AGENDADA = "VISITA_AGENDADA", "Visita agendada"
        SEM_RESPOSTA = "SEM_RESPOSTA", "Sem resposta"
        DESCARTADO = "DESCARTADO", "Descartado"

    # o que o corretor/gestor pode anotar — "pendente" é só o estado inicial
    RETORNOS_ANOTAVEIS = [
        Retorno.FALEI, Retorno.VISITA_AGENDADA, Retorno.SEM_RESPOSTA, Retorno.DESCARTADO,
    ]

    imovel = models.ForeignKey(
        "imoveis.Imovel", on_delete=models.CASCADE, related_name="contatos"
    )
    corretor = models.ForeignKey(
        "contas.Usuario",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="contatos_recebidos",
    )
    canal = models.CharField(max_length=20, choices=Canal.choices)
    criado_em = models.DateTimeField(auto_now_add=True, db_index=True)
    retorno = models.CharField(max_length=20, choices=Retorno.choices, default=Retorno.PENDENTE)
    retorno_em = models.DateTimeField("retorno anotado em", null=True, blank=True)

    class Meta:
        verbose_name = "contato"
        verbose_name_plural = "contatos"
        ordering = ["-criado_em", "-id"]

    def __str__(self):
        return f"{self.get_canal_display()} sobre {self.imovel} em {self.criado_em:%d/%m/%Y}"

    def anotar_retorno(self, retorno):
        if retorno not in self.RETORNOS_ANOTAVEIS:
            raise ValidationError(
                "Retorno inválido. Opções: falei, visita agendada, sem resposta ou descartado."
            )
        self.retorno = retorno
        self.retorno_em = timezone.now()
        self.save(update_fields=["retorno", "retorno_em"])
