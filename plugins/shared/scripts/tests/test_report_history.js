// Executed by test_report_history.py; JSON is decoded as data, never evaluated.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
    constructor(tag, nodeType = 1, text = '') {
        this.tag = tag;
        this.nodeType = nodeType;
        this.childNodes = [];
        this.attributes = {};
        this.listeners = {};
        this.style = {};
        this.className = '';
        this.text = text;
        this.classList = {
            contains: name => this.className.split(' ').includes(name),
            toggle: name => {
                const classes = this.className.split(' ').filter(Boolean);
                this.className = (classes.includes(name) ? classes.filter(item => item !== name) : [...classes, name]).join(' ');
            },
        };
    }
    get children() { return this.childNodes.filter(node => node.nodeType === 1); }
    appendChild(child) { child.parent = this; this.childNodes.push(child); return child; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name] ?? this[name] ?? null; }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    closest(tag) { return this.tag === tag ? this : this.parent?.closest(tag); }
    get nextElementSibling() {
        const siblings = this.parent?.children || [];
        return siblings[siblings.indexOf(this) + 1] || null;
    }
    set textContent(value) { this.text = String(value); this.childNodes = []; }
    get textContent() { return this.text + this.childNodes.map(child => child.textContent).join(''); }
    set innerHTML(value) { throw new Error('History must not parse HTML: ' + value); }
}
function descendants(element) { return element.childNodes.flatMap(child => [child, ...descendants(child)]); }
function withClass(element, name) { return descendants(element).filter(child => child.classList.contains(name)); }
function tags(element, name) { return descendants(element).filter(child => child.tag === name); }
const document = {createElement: tag => new Element(tag), createTextNode: text => new Element('#text', 3, String(text))};
const html = fs.readFileSync(process.argv[2], 'utf8');
const data = JSON.parse(html.split('window.REPORT_DATA = ', 2)[1].split(';</script>', 1)[0]);
const script = html.slice(html.lastIndexOf('<script>') + '<script>'.length, html.lastIndexOf('</script>'));
// Exercise the saved report's real rendering functions, without unrelated
// browser wiring or any network/model calls.
const definitions = script.slice(script.indexOf('// === DOM helpers ==='), script.indexOf('// === Index image info'));
const context = {document, URL, window: {REPORT_DATA: data}};
vm.runInNewContext('var D = window.REPORT_DATA; var _gc = 0; var JIRA_BASE = "https://issues.redhat.com";\n' + definitions +
    '\nwindow.historyTest = {renderIssueTable, renderIssueHistory, renderHistoryCoverage};', context);
const probe = context.window.historyTest;
const original = JSON.stringify(data);
const current = data.releases_data['4.20'].issues[0];
const savedHistory = data.history;
const untrusted = '</script><!-- <img src=x onerror="throw 1"> & historical evidence';

function renderHistory(history) {
    const parent = new Element('div');
    probe.renderIssueHistory(parent, {history});
    return withClass(parent, 'issue-history')[0];
}
function summaryOf(card) { return withClass(card, 'history-summary')[0]; }
function assertCompact(card) {
    assert.equal(tags(card, 'h4').length, 0, 'history has no per-job headings');
    assert.equal(tags(card, 'table').length, 0, 'history has no timeline tables');
    assert.equal(tags(card, 'details').length, 0, 'history has no debug disclosures');
    assert.equal(tags(card, 'p').length, 1, 'only the compact issue summary is a paragraph');
    assert.equal(withClass(card, 'history-timeline').length, 0);
    assert.equal(withClass(card, 'history-evidence').length, 0);
    for (const item of withClass(card, 'history-match')) {
        assert.equal(item.tag, 'li');
        assert(tags(item, 'a').length <= 1, 'each match has at most one source report link');
        assert.equal(tags(item, 'ul').length, 0, 'matches never list individual affected jobs');
    }
}
function assertSafeLinks(element) {
    for (const link of tags(element, 'a')) {
        assert(/^https?:\/\//i.test(link.href));
        assert.equal(link.target, '_blank');
        assert.equal(link.rel, 'noopener noreferrer');
    }
    assert.equal(tags(element, 'img').length, 0, 'untrusted text never becomes markup');
    assert.equal(tags(element, 'script').length, 0);
}

const coverage = new Element('div');
probe.renderHistoryCoverage(coverage);
const banner = withClass(coverage, 'history-coverage')[0];
assert(banner, 'global coverage is visible independently of issue expansion');
assert.equal(banner.id, 'history-coverage');
assert.equal(banner.tag, 'p', 'coverage is one quiet line, not an expanded disclosure');
assert.equal(banner.children.length, 0);
assert(/partial/i.test(banner.textContent));
assert(banner.textContent.includes('14d'));
assert(banner.textContent.length < 100);
for (const gap of data.history.coverage.gaps) assert(!banner.textContent.includes(gap));

const periodic = new Element('div');
probe.renderIssueTable(periodic, [current], 'release-4.20', 'Release 4.20');
const card = withClass(periodic, 'issue-history')[0];
assert(card, 'periodic issue details include saved history');
assertCompact(card);
const summary = summaryOf(card).textContent;
assert(summary.includes('8d ago'), 'first-seen age uses the saved history anchor');
assert(summary.includes('2 runs'));
assert(!summary.includes('20'), 'uncertain match occurrences are not added to accepted counts');
assert(!summary.includes('22'));
assert(summary.includes(current.history.pattern));
assert(/partial/i.test(summary));
assert(/may predate/i.test(summary), 'window uncertainty stays concise but visible');
const matches = withClass(card, 'history-match');
assert.equal(matches.length, 2);
const [acceptedMatch, possibleMatch] = matches;
assert(acceptedMatch.textContent.includes('93%'));
assert(acceptedMatch.textContent.includes('8d ago'));
assert(acceptedMatch.textContent.includes('2 runs'));
assert(acceptedMatch.textContent.includes(current.history.matches[0].title));
for (const release of ['4.18', '4.20', '4.21', '4.22', '5.0', '5.1']) {
    assert(acceptedMatch.textContent.includes(release), 'cross-release label is visible: ' + release);
}
assert(acceptedMatch.textContent.includes('Releases '));
assert(acceptedMatch.textContent.includes('PR #7'));
assert(!acceptedMatch.textContent.includes('release:'), 'internal scope syntax is not rendered');
assert(!acceptedMatch.textContent.includes(untrusted), 'reasons and invalid scope labels are not prose');
assert.equal(acceptedMatch.title, untrusted, 'the short reason remains available as inert tooltip text');
assert(!/possible/.test(acceptedMatch.textContent));
assert(possibleMatch.textContent.includes('65%'));
assert(possibleMatch.textContent.includes('20 runs'));
assert(/possible/i.test(possibleMatch.textContent), 'uncertain candidates cannot read as accepted causes');
for (const gap of current.history.gaps) assert(!card.textContent.includes(gap));
assert.equal(tags(acceptedMatch, 'a').length, 1);
assert.equal(tags(acceptedMatch, 'a')[0].href, 'https://example.invalid/doctor/1');
assert.equal(tags(possibleMatch, 'a').length, 0, 'unsafe source reports are not linked');
assert(!tags(card, 'a').some(link => link.href.includes('/run/')), 'historical job lists and timelines are absent');
assertSafeLinks(card);

const title = withClass(periodic, 'col-title')[0];
assert(!withClass(periodic, 'detail-row')[0].classList.contains('show'));
title.listeners.click.call(title);
assert(withClass(periodic, 'detail-row')[0].classList.contains('show'), 'existing issue expansion still works');
const pr = new Element('div');
probe.renderIssueTable(pr, data.pr_data.prs[0].issues, 'pr-7', 'PR #7');
assert.equal(withClass(pr, 'issue-history').length, 1);
assert.equal(withClass(pr, 'issue-history')[0].textContent, card.textContent, 'periodics and PRs share the consumer');
assert.equal(JSON.stringify(data), original, 'rendering preserves saved history, timelines, gaps and counts');

const accepted = current.history.matches[0];
const deduplicated = renderHistory({...current.history, matches: [
    accepted, JSON.parse(JSON.stringify(accepted)),
    {...accepted, scopes: ['release:6.0'], reports: [{url: 'https://example.invalid/doctor/other'}]},
]});
assert.equal(withClass(deduplicated, 'history-match').length, 2, 'only identical records collapse, not similar titles or separate same-cause verdicts');
assert(withClass(deduplicated, 'history-match')[1].textContent.includes('Release 6.0'));
assert(summaryOf(deduplicated).textContent.includes('2 runs'), 'UI deduplication never recomputes accepted counts');
const mainRelease = renderHistory({...current.history, matches: [{...accepted, scopes: ['release:main']}]});
assert(withClass(mainRelease, 'history-match')[0].textContent.includes('Release main'), 'main is an explicit clean release label');
assert(!mainRelease.textContent.includes('release:main'));

const old = new Element('div');
probe.renderIssueHistory(old, {title: 'old report without history'});
assert.equal(old.children.length, 0, 'history remains optional for older reports');
probe.renderHistoryCoverage(null);
for (const status of ['incomplete', 'unavailable']) {
    const missing = renderHistory({status, gaps: ['private diagnostic detail'], matches: []});
    assertCompact(missing);
    assert(/partial|unavailable/i.test(summaryOf(missing).textContent));
    assert(!/\b0 runs\b/.test(summaryOf(missing).textContent), 'an unknown count is not zero');
    assert(!missing.textContent.includes('private diagnostic detail'));
}
for (const pattern of ['intermittent evidence', 'persistent in observed runs', 'single observed occurrence', 'insufficient evidence']) {
    assert(summaryOf(renderHistory({status: 'complete', pattern, occurrences: 1})).textContent.includes(pattern));
}
const unsupported = renderHistory({status: 'complete', pattern: 'proven harmless flake', occurrences: 1,
    matches: [{verdict: 'future_verdict', score: 999, title: untrusted, reason: untrusted.repeat(10)}]});
assert(!summaryOf(unsupported).textContent.includes('proven harmless flake'));
assert(summaryOf(unsupported).textContent.includes('insufficient evidence'));
assert(!unsupported.textContent.includes('999%'));
assert(/possible/.test(withClass(unsupported, 'history-match')[0].textContent));
assert(withClass(unsupported, 'history-match')[0].textContent.includes(untrusted));
assert(withClass(unsupported, 'history-match')[0].title.length <= 240, 'reasons cannot expand into long tooltips');
assertSafeLinks(unsupported);
const rejected = renderHistory({status: 'complete', occurrences: 0,
    matches: [{verdict: 'different_cause', title: 'Known unrelated cause', score: 99}]});
assert.equal(withClass(rejected, 'history-match').length, 0, 'known different causes are not possible matches');

for (const history of [
    {coverage: {complete: false}},
    {coverage: {complete: true, gaps: ['private retrieval detail']}},
    {coverage: {complete: true, reports_found: 4, reports_loaded: 3}},
    {coverage: {complete: true}, gaps: ['private assessment detail']},
    {status: 'unavailable', coverage: {complete: false}},
]) {
    data.history = history;
    const parent = new Element('div');
    probe.renderHistoryCoverage(parent);
    assert(/partial|unavailable/i.test(parent.textContent));
    assert.equal(tags(parent, 'details').length, 0);
    const issueCard = renderHistory({status: 'complete', occurrences: 0, matches: []});
    assert(/partial|unavailable/i.test(summaryOf(issueCard).textContent), 'global gaps also qualify an otherwise complete issue');
}
data.history = {coverage: {complete: true, gaps: [], reports_found: 3, reports_loaded: 3}};
const complete = new Element('div');
probe.renderHistoryCoverage(complete);
assert(/complete/i.test(complete.textContent));
assert(!/partial|unavailable/i.test(complete.textContent));
const completeIssue = renderHistory({status: 'complete', occurrences: 0, matches: []});
assert(!/partial/.test(summaryOf(completeIssue).textContent));
const issueGap = renderHistory({status: 'complete', gaps: ['private diagnostic detail']});
assert(/partial/.test(summaryOf(issueGap).textContent));
data.history = savedHistory;

for (const [date, age] of [
    ['2026-10-20', '8d ago'], ['2026-10-28', 'today'],
    ['2026-10-28T11:00:00Z', '1h ago'], ['2026-10-28T11:59:00Z', '1m ago'],
]) {
    const dated = renderHistory({status: 'complete', first_seen: date, occurrences: 1});
    assert(summaryOf(dated).textContent.includes(age));
}
for (const date of ['2026-02-30', '2026', 'not-a-date']) {
    const invalid = renderHistory({first_seen: date});
    assert(!summaryOf(invalid).textContent.includes(date), 'invalid dates cannot become apparently valid ages');
    assert(/unknown/.test(summaryOf(invalid).textContent));
}
const future = renderHistory({first_seen: '2026-10-29', occurrences: 1});
assert(!summaryOf(future).textContent.includes('-1d'));
assert(/outside/.test(summaryOf(future).textContent));
data.history = undefined;
const unanchored = renderHistory({first_seen: '2026-10-20', occurrences: 1});
assert(summaryOf(unanchored).textContent.includes('2026-10-20'), 'no invented relative age without a saved anchor');
const absentCoverage = new Element('div');
probe.renderHistoryCoverage(absentCoverage);
assert.equal(absentCoverage.children.length, 0);
data.history = savedHistory;

for (const url of ['javascript:alert(1)', 'data:text/html,x', '//example.invalid/run', 'ftp://example.invalid/run',
    'https://', 'https://user:password@example.invalid/run', 'https://example.invalid/\nrun',
    ' https://example.invalid/run', 'https://example.invalid/\u0000run']) {
    const unsafe = renderHistory({matches: [{...accepted, title: untrusted, reports: [{url}]}]});
    assert.equal(tags(unsafe, 'a').length, 0, 'unsafe report URL remains inert: ' + JSON.stringify(url));
    assertSafeLinks(unsafe);
}
for (const url of ['https://example.invalid/run?q=%3Cscript%3E', 'http://example.invalid/report']) {
    const safe = renderHistory({matches: [{...accepted, reports: [{url}]}]});
    assert.equal(tags(safe, 'a').length, 1);
    assert.equal(tags(safe, 'a')[0].href, url);
    assertSafeLinks(safe);
}
console.log('PASS history renderer: compact matches, cross-release labels, uncertainty, unchanged counts and safe report links');
