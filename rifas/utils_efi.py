# rifas/utils_efi.py
from .models import EfiConfig, Rifa, Empresa


def get_efi_config_for_rifa(rifa: "Rifa") -> EfiConfig | None:
    # 1. config específica da rifa
    if rifa.efi_config_id:
        return rifa.efi_config

    # 2. config da empresa da rifa
    if rifa.empresa_id:
        cfg = EfiConfig.objects.filter(empresa=rifa.empresa, ativo=True).first()
        if cfg:
            return cfg

    # 3. config global
    return EfiConfig.objects.filter(empresa__isnull=True, rifa__isnull=True, ativo=True).first()
