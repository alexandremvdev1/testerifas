# rifas/webhooks.py
from __future__ import annotations
import json
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone

try:
    # só se teus modelos existirem (WebhookEvent e Pedido são opcionais)
    from .models import WebhookEvent, Pedido
except Exception:
    WebhookEvent = None
    Pedido = None


@csrf_exempt
def webhook_provider(request, provider_key: str):
    """
    Endpoint genérico de webhook:
    - GET/HEAD -> só confirma que está no ar (EFI usa isso na validação)
    - POST     -> processa o evento
    """
    # 1) EFI pode só "pingar" a URL → responde 200
    if request.method in ("GET", "HEAD"):
        return HttpResponse("ok", status=200)

    # 2) Só vamos processar de fato se for POST
    if request.method != "POST":
        return HttpResponseBadRequest("Somente POST")

    # 3) Tenta decodificar JSON; se falhar, usa form-data
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        payload = request.POST.dict()

    # 4) Log básico do evento (opcional)
    if WebhookEvent:
        try:
            event_id = str(
                payload.get("id")
                or payload.get("data", {}).get("id")
                or int(timezone.now().timestamp())
            )
            WebhookEvent.objects.create(
                provider=provider_key,
                event_id=event_id,
                raw=payload,
            )
        except Exception:
            # não quebra o webhook por causa do log
            pass

    # 5) Atualização de pedido (bem simples, só se vier protocolo + status)
    if Pedido:
        protocolo = payload.get("protocolo") or payload.get("data", {}).get("protocolo")
        status = (payload.get("status") or payload.get("data", {}).get("status") or "").lower()

        if protocolo and status:
            try:
                p = Pedido.objects.get(protocolo=protocolo)
                # aqui você adapta pros status da EFI quando souber o payload real
                if status in {"approved", "paid", "success", "pago"}:
                    p.status = Pedido.PAGO
                    p.pago_em = timezone.now()
                    p.save(update_fields=["status", "pago_em"])
                    # se tiver relação de números
                    if hasattr(p, "numeros"):
                        p.numeros.update(status="pago")
            except Pedido.DoesNotExist:
                pass
            except Exception:
                pass

    return JsonResponse({"ok": True, "provider": provider_key}, status=200)
