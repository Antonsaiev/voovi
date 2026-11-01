import importlib.util, os
from types import SimpleNamespace
from unittest.mock import MagicMock,patch
from django.conf import settings
if not settings.configured:settings.configure(DEFAULT_CHARSET='utf-8')
spec=importlib.util.spec_from_file_location('legacy_guard',str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'compat/legacy_refund_guard.py'));module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
from django.db import DatabaseError
for mode in ['get','plain','linked','missing-table','outage','changed-source']:
 lock=MagicMock();cursor=lock.cursor.return_value.__enter__.return_value
 cursor.fetchall.return_value=[(83,)]
 events=[]
 cursor.execute.side_effect=lambda sql,params=None:events.append('metadata' if 'payment_refund_links' in sql else 'lock' if 'FOR UPDATE' in sql else 'sql')
 cursor.fetchone.side_effect=[('IN',),(1,) if mode=='linked' else None]
 if mode=='changed-source':cursor.fetchone.side_effect=[('CHANGED',)]
 if mode in ('missing-table','outage'):
  def execute(sql,params=None):
   if 'payment_refund_links' in sql:raise DatabaseError(1146 if mode=='missing-table' else 2006,'fixture')
   events.append('lock' if 'FOR UPDATE' in sql else 'sql')
  cursor.execute.side_effect=execute
 called=[]
 def original(request):called.append(True);events.append('legacy');return 'unchanged'
 request=SimpleNamespace(method='GET' if mode=='get' else 'POST',POST={'random_key':'IN'})
 with patch.object(module,'connection') as connection:
  connection.copy.return_value=lock
  try:response=module.guard_refund_linked_payments(original)(request)
  except DatabaseError:
   assert mode=='outage' and not called
  else:
   if mode=='linked':assert response.status_code==302 and 'extended=1' in response.url and not called
   elif mode=='changed-source':assert response.status_code==409 and not called
   else:assert response=='unchanged' and called==[True]
  if mode!='get':lock.rollback.assert_called_once();lock.close.assert_called_once()
  if mode=='plain':assert events.index('lock')<events.index('metadata')<events.index('legacy')
print('LEGACY_GUARD_TESTS 6 PASS; no SQL/HTTP/business writes')
