/* "What kind of account is it?" on the Add account page.

   A new person picks one of six plain kinds, then the exact type inside it, instead of scanning a dropdown of 34.
   This only enhances the page: the type dropdown (#id_account_type) stays in the form as the one source of truth, and
   everything that reads it (the step 2 fields, the server) sees the choice made here. Without this script the page
   shows that dropdown as it always did.

   Stages:  kinds  ->  types (the types inside one kind)  ->  chosen (the usual fields appear).  "Change" goes back. */
(function () {
  'use strict';

  var box = document.getElementById('typeChooser');
  var dataEl = document.getElementById('type-categories');
  var select = document.getElementById('id_account_type');
  var fields = document.getElementById('accountBasicsFields');
  if (!box || !dataEl || !select || !fields) return;           // nothing to enhance: the dropdown stays in charge

  var categories;
  try { categories = JSON.parse(dataEl.textContent); } catch (e) { return; }

  var kinds = document.getElementById('typeKinds');
  var choices = document.getElementById('typeChoices');
  var chosen = document.getElementById('typeChosen');
  var list = document.getElementById('typeChoicesList');
  var title = document.getElementById('typeChoicesTitle');
  var chosenLabel = document.getElementById('typeChosenLabel');
  var nextBtn = document.getElementById('nextBtn');
  var typeColumn = select.closest('[class*="col-"]');
  var which = box.getAttribute('data-label-which') || '';

  function show(el, on) { if (el) el.classList.toggle('d-none', !on); }

  function optionLabel(code) {
    var option = select.querySelector('option[value="' + code + '"]');
    return option ? option.textContent.trim() : code;
  }

  function stage(name) {
    show(kinds, name === 'kinds');
    show(choices, name === 'types');
    show(chosen, name === 'chosen');
    show(fields, name === 'chosen');
    show(typeColumn, false);                                    // the chosen type is shown above instead
    if (nextBtn) nextBtn.disabled = name !== 'chosen';          // nothing to continue with until a type is picked
  }

  function showTypes(category) {
    list.innerHTML = '';
    title.textContent = which ? which + ' · ' + category.title : category.title;
    category.codes.forEach(function (code) {
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'btn btn-outline-secondary type-option';
      button.textContent = optionLabel(code);
      button.addEventListener('click', function () { choose(code); });
      list.appendChild(button);
    });
    stage('types');
    title.focus();
  }

  function choose(code) {
    select.value = code;
    // The step 2 fields and the styled dropdown both listen for this
    select.dispatchEvent(new Event('change', { bubbles: true }));
    chosenLabel.textContent = optionLabel(code);
    stage('chosen');
    var name = document.getElementById('id_name');
    if (name) name.focus();
  }

  function backToKinds() {
    stage('kinds');
    var first = kinds.querySelector('.type-card');
    if (first) first.focus();
  }

  kinds.querySelectorAll('.type-card').forEach(function (card) {
    card.addEventListener('click', function () {
      var key = card.getAttribute('data-category');
      var category = categories.filter(function (c) { return c.key === key; })[0];
      if (category) showTypes(category);
    });
  });
  document.getElementById('typeChoicesBack').addEventListener('click', backToKinds);
  document.getElementById('typeChosenChange').addEventListener('click', backToKinds);

  show(box, true);
  stage('kinds');
})();
