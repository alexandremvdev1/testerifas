# rifas/forms.py
from django import forms
from django.utils import timezone
from .models import Rifa
from django.core.exceptions import ValidationError

class LoginForm(forms.Form):
    username = forms.CharField(label="Usuário")
    password = forms.CharField(widget=forms.PasswordInput, label="Senha")

class RifaForm(forms.ModelForm):
    class Meta:
        model = Rifa
        fields = [
            "titulo",
            "slug",
            "descricao",
            "banner",
            "preco_numero",
            "quantidade_numeros",
            "inicio_vendas",
            "fim_vendas",
            "ativo",
            "permitir_numero_escolhido",
            "limite_por_pedido",
            "minutos_expiracao_reserva",
            "mostrar_top_compradores",
        ]
        widgets = {
            "descricao": forms.Textarea(attrs={"rows": 4, "class": "form-control"}),
            # esses widgets aceitam o formato do input HTML5: 2025-10-29T14:00
            "inicio_vendas": forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"}),
            "fim_vendas": forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"}),
        }

    def clean(self):
        cleaned = super().clean()
        ini = cleaned.get("inicio_vendas")
        fim = cleaned.get("fim_vendas")
        if ini and fim and fim <= ini:
            raise ValidationError("A data/hora de fim das vendas deve ser posterior ao início.")
        return cleaned

    def clean_slug(self):
        s = (self.cleaned_data.get("slug") or "").strip().lower()
        if " " in s:
            s = s.replace(" ", "-")
        return s

from .models import RifaFinanceiro

from .models import RifaFinanceiro

class RifaFinanceiroForm(forms.ModelForm):
    class Meta:
        model = RifaFinanceiro
        fields = [
            "custo_premio",
            "premio_top_comprador",
            "outras_despesas",
            "taxa_admin_percentual",
            "taxa_admin_fixa",
        ]
        widgets = {
            "custo_premio": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "premio_top_comprador": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "outras_despesas": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "taxa_admin_percentual": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "taxa_admin_fixa": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
        }
