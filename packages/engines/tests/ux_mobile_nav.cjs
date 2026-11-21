// The narrow-screen menu contract, shared by the Power BI, ADF and SQL Agent browser checks.
// checkMobileNav(browser, url, {toggle, label, section(page) -> current section label, sectionButtons})
// At 390 and 375 px: the menu starts collapsed and names the current section; the button has aria-expanded and
// aria-controls; it works by keyboard (Enter and Space); opening leaves focus on the button; Tab reaches the
// sections; Escape closes it and returns focus to the button; choosing a section by keyboard closes it, names the
// new section and moves focus to the content; a click outside or crossing the breakpoint closes it; no section
// scrolls sideways. At desktop
// width the button is hidden, the sections show, and Escape changes nothing.
const assert = require('node:assert/strict');

async function checkMobileNav(browser, url, o) {
  const report = [];
  for (const width of [390, 375]) {
    const context = await browser.newContext({viewport: {width, height: 800}});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(url);
    const toggle = page.locator(o.toggle);
    const at = what => `${what} at ${width} px`;
    const expanded = async () => toggle.getAttribute('aria-expanded');
    const focusedId = () => page.evaluate(() => document.activeElement?.id || document.activeElement?.tagName);
    const controls = await toggle.getAttribute('aria-controls');
    assert.ok(controls, at('the button names what it controls'));
    const sections = page.locator('#' + controls);
    assert.ok(await toggle.isVisible(), at('the menu button shows'));
    assert.equal(await expanded(), 'false', at('the menu starts collapsed'));
    assert.equal(await sections.isVisible(), false, at('the sections are hidden while collapsed'));
    assert.equal((await page.locator(o.label).innerText()).trim(), await o.section(page), at('the button names the current section'));

    await toggle.focus();
    await page.keyboard.press('Enter');
    assert.equal(await expanded(), 'true', at('Enter opens the menu'));
    assert.ok(await sections.isVisible(), at('the sections show when open'));
    assert.equal(await focusedId(), await toggle.getAttribute('id'), at('opening leaves focus on the button'));
    await page.keyboard.press('Tab');
    assert.ok(await page.evaluate(id => document.getElementById(id).contains(document.activeElement), controls), at('Tab moves into the sections'));
    await page.keyboard.press('Escape');
    assert.equal(await expanded(), 'false', at('Escape closes the menu'));
    assert.equal(await focusedId(), await toggle.getAttribute('id'), at('Escape returns focus to the button'));

    await page.keyboard.press(' ');
    assert.equal(await expanded(), 'true', at('Space opens the menu'));
    // Tab to a section that is not the current one, and choose it with Enter.
    const target = await page.evaluate(([controls, selector]) => {
      const current = document.querySelector('[aria-current]');
      const buttons = [...document.getElementById(controls).querySelectorAll(selector)]
        .filter(b => b !== current && b.tabIndex >= 0 && !b.disabled && b.getAttribute('aria-disabled') !== 'true');
      return buttons.length ? buttons.at(-1).id : null;
    }, [controls, o.sectionButtons]);
    assert.ok(target, at('there is another section to choose'));
    for (let i = 0; i < 40 && (await focusedId()) !== target; i++) await page.keyboard.press('Tab');
    assert.equal(await focusedId(), target, at('Tab reaches ' + target));
    const name = (await page.locator('#' + target).evaluate(b => b.childNodes[0].textContent)).trim();
    await page.keyboard.press('Enter');
    assert.equal(await expanded(), 'false', at('choosing a section closes the menu'));
    assert.equal(await page.locator('#' + target).getAttribute('aria-current') !== null, true, at('the chosen section is current'));
    assert.equal((await page.locator(o.label).innerText()).trim(), name, at('the button names the chosen section'));
    assert.equal(await focusedId(), 'main', at('focus moves to the chosen section\'s content'));

    await toggle.click();
    assert.equal(await expanded(), 'true');
    await page.locator('main').click({position: {x: 5, y: 5}});
    assert.equal(await expanded(), 'false', at('a click outside closes the menu'));
    // Crossing the breakpoint while open (a rotated phone, a resized split screen) closes it.
    await toggle.click();
    assert.equal(await expanded(), 'true');
    await page.setViewportSize({width: 1440, height: 800});
    // media-query changes are delivered with the next rendering frame, not synchronously with the resize
    await page.waitForFunction(sel => document.querySelector(sel).getAttribute('aria-expanded') === 'false', o.toggle, {timeout: 3000})
      .catch(() => {});
    await page.setViewportSize({width, height: 800});
    assert.equal(await expanded(), 'false', at('widening past the breakpoint and back leaves the menu closed'));
    assert.equal(await sections.isVisible(), false, at('the sections are hidden again'));
    // Every section, opened from the menu, fits the screen.
    const ids = await page.evaluate(([controls, selector]) => [...document.getElementById(controls).querySelectorAll(selector)]
      .filter(b => b.tabIndex >= 0 && b.getAttribute('aria-disabled') !== 'true').map(b => b.id), [controls, o.sectionButtons]);
    for (const id of ids) {
      await toggle.click();
      await page.locator('#' + id).click();
      const scroll = await page.evaluate(() => document.documentElement.scrollWidth);
      assert.ok(scroll <= width, at(id + ' does not scroll sideways (' + scroll + ')'));
    }
    assert.deepEqual(errors, [], at('no page errors'));
    await context.close();
    report.push(width);
  }
  const context = await browser.newContext({viewport: {width: 1440, height: 900}});
  const page = await context.newPage();
  await page.goto(url);
  assert.equal(await page.locator(o.toggle).isVisible(), false, 'the menu button is hidden on a wide screen');
  const controls = await page.locator(o.toggle).getAttribute('aria-controls');
  assert.ok(await page.locator('#' + controls).isVisible(), 'the sections show on a wide screen');
  await page.locator(o.sectionButtons).first().focus();
  await page.keyboard.press('Escape');
  assert.ok(await page.locator('#' + controls).isVisible(), 'Escape changes nothing on a wide screen');
  await context.close();
  return report;
}

module.exports = {checkMobileNav};
