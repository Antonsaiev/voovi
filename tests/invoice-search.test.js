// Исполняем реальные обработчики поиска без браузера, сети и финансовых записей.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const template = fs.readFileSync(path.join(__dirname, '../analitic/templates/payments/invoice_seach_payment/index.html'), 'utf8');
const source = template.match(/<script>([\s\S]*?)<\/script>/)[1];
const result = {textContent: '', innerHTML: ''};
const handlers = {};
const requests = [];
let submit;
let searchRefreshes = 0;
const form = {};
const document = {getElementById: id => { assert.equal(id, 'result'); return result; }};
function $(target) {
    if (target === document) return {on: (event, selector, callback) => {handlers[selector] = callback;}};
    if (target === '#poisk_payment') return {submit: callback => {submit = callback;}, trigger: () => {searchRefreshes++;}};
    if (typeof target === 'string') return {val: () => ''};
    if (target === form) return {serializeArray: () => [{name: 'n_schet', value: 'N1'}]};
    return {data: key => target[key], prop: () => {}};
}
$.ajax = options => {
    const request = {options, aborted: false, abort() {
        this.aborted = true;
        options.error({}, 'abort');
        options.complete();
    }};
    requests.push(request);
    if (options.beforeSend) options.beforeSend();
    return request;
};
vm.runInNewContext(source, {$, document, window: {confirm: () => true}, alert: () => {}});
const event = {preventDefault() {}};

submit.call(form, event);
assert.equal(result.textContent, 'Загрузка, подождите');
requests.at(-1).options.error({responseText: JSON.stringify({message: 'Неоднозначный номер <N1>'})}, 'error');
assert.equal(result.textContent, 'Неоднозначный номер <N1>');
assert.equal(result.innerHTML, ''); // Ошибка выводится текстом, без исполнения разметки.

requests.at(-1).options.error({responseText: '<html>private server traceback</html>'}, 'error');
assert.equal(result.textContent, 'Не удалось найти оплаты. Повторите поиск.');
submit.call(form, event);
assert.equal(requests[0].aborted, true);
assert.equal(result.textContent, 'Загрузка, подождите');
requests.at(-1).options.success('<table>Оплата найдена</table>');
assert.equal(result.innerHTML, '<table>Оплата найдена</table>');

handlers['#buthid .check-payment-action'].call({
    action: 'refund', 'schet-rand': 'R1', 'expected-checks': 'a'.repeat(64)
}, event);
const confirmation = requests.at(-1).options;
assert.equal(confirmation.url, '/payments/InvoiceSearchPaymentCheck');
assert.equal(confirmation.data.action, 'refund');
assert.equal(confirmation.data.expected_checks, 'a'.repeat(64));
assert.equal(confirmation.data.schet_rand, 'R1');
confirmation.error({status: 409, responseJSON: {message: 'Состояние чеков изменилось'}});
assert.equal(searchRefreshes, 1);
console.log('Invoice search: errors, aborted requests, results and explicit receipt action — OK');
