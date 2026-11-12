/* On the Add account page: when the chosen type is an investment or a deposit, one line says how money gets in and out
   of it (Move Money) and, for a deposit, what to do when it matures. A non-zero opening balance adds a second line. */
(function () {
  'use strict';

  var dataEl = document.getElementById('investment-types');
  var wrap = document.getElementById('investmentHintWrap');
  var hint = document.getElementById('investmentHint');
  var select = document.getElementById('id_account_type');
  if (!dataEl || !wrap || !hint || !select) return;

  var types;
  try { types = JSON.parse(dataEl.textContent); } catch (e) { return; }
  var text = hint.querySelector('.tmr-invest-note__text');
  var balance = document.getElementById('id_balance');

  function refresh() {
    var type = select.value;
    var kind = types.deposit.indexOf(type) !== -1 ? 'deposit' : types.market.indexOf(type) !== -1 ? 'market' : '';
    wrap.classList.toggle('d-none', !kind);
    if (!kind) return;
    var opening = balance ? parseFloat(String(balance.value || '').replace(/[^0-9.]/g, '')) : 0;
    text.innerHTML = hint.dataset[kind] + (opening > 0 ? ' ' + hint.dataset.opening : '');
  }

  select.addEventListener('change', refresh);
  if (balance) ['input', 'change'].forEach(function (name) { balance.addEventListener(name, refresh); });
  refresh();
})();
