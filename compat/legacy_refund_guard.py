"""Совместимость прежней аналитики с новыми связями банковских возвратов."""
from functools import wraps
from urllib.parse import urlencode

from django.db import connection, DatabaseError
from django.http import HttpResponse, HttpResponseRedirect


PAYMENT_URL = 'https://analitic.voovi.ru/payments/beznal/add'


def guard_refund_linked_payments(view):
    """Старый разнос сохраняется, связанные источники открываются в новой аналитике."""
    @wraps(view)
    def guarded(request, *args, **kwargs):
        key = request.POST.get('random_key') if request.method == 'POST' else None
        if not key:
            return view(request, *args, **kwargs)
        # Отдельное соединение удерживает только исходный платёж. Старые записи
        # счетов/КВО/чеков выполняются прежним кодом и с прежним autocommit.
        lock = connection.copy(alias='legacy_refund_source_guard')
        try:
            with lock.cursor() as cursor:
                cursor.execute('SELECT ID FROM payment WHERE random_key=%s ORDER BY ID LIMIT 2', [key])
                ids = [row[0] for row in cursor.fetchall()]
                if len(ids) != 1:
                    return view(request, *args, **kwargs)
                cursor.execute('START TRANSACTION')
                cursor.execute('SELECT random_key FROM payment WHERE ID=%s FOR UPDATE', ids)
                source = cursor.fetchone()
                if source is None or source[0] != key:
                    return HttpResponse('Платежка изменилась. Обновите страницу.', status=409)
                try:
                    cursor.execute('SELECT 1 FROM payment_refund_links '
                                   'WHERE incoming_payment_id=%s OR outgoing_payment_id=%s LIMIT 1',
                                   [ids[0], ids[0]])
                    linked = cursor.fetchone() is not None
                except DatabaseError as error:
                    if not error.args or error.args[0] != 1146:
                        raise
                    linked = False
                if linked:
                    return HttpResponseRedirect(PAYMENT_URL + '?' + urlencode(
                        {'random_key': key, 'extended': '1'}))
                return view(request, *args, **kwargs)
        finally:
            try:
                lock.rollback()
            finally:
                lock.close()
    return guarded
