<template>
  <private-view title="UTM Stats">
    <template #navigation>
      <div class="nav nav--premium">
        <div class="nav__brand">
          <div class="nav__brand-logo">SVPN</div>
          <div>
            <div class="nav__brand-title">UTM Stats</div>
            <div class="nav__brand-subtitle">Конверсия по источникам</div>
          </div>
        </div>

        <div class="nav__section">
          <div class="nav__section-title">Навигация</div>
          <router-link class="nav__item nav__item--active" :to="{ path: '/tvpn-utm-stats' }">
            <span class="nav__item-icon"><v-icon name="trending_up" /></span>
            <span class="nav__item-label">UTM Stats</span>
          </router-link>
          <router-link class="nav__item" :to="{ path: '/tvpn-promo-studio' }">
            <span class="nav__item-icon"><v-icon name="workspace_premium" /></span>
            <span class="nav__item-label">Promo Studio</span>
          </router-link>
          <router-link class="nav__item" :to="{ path: '/tvpn-segment-studio' }">
            <span class="nav__item-icon"><v-icon name="campaign" /></span>
            <span class="nav__item-label">Segment Studio</span>
          </router-link>
          <router-link class="nav__item" :to="{ path: '/tvpn-tariff-studio' }">
            <span class="nav__item-icon"><v-icon name="tune" /></span>
            <span class="nav__item-label">Tariff Studio</span>
          </router-link>
          <router-link class="nav__item" :to="{ path: '/tvpn-home' }">
            <span class="nav__item-icon"><v-icon name="home" /></span>
            <span class="nav__item-label">Главная</span>
          </router-link>
        </div>
      </div>
    </template>

    <template #actions>
      <div class="bucket-toggle" role="group" aria-label="Шаг графика">
        <button
          type="button"
          :class="['bucket-toggle__btn', bucket === 'day' ? 'bucket-toggle__btn--active' : '']"
          @click="setBucket('day')"
        >День</button>
        <button
          type="button"
          :class="['bucket-toggle__btn', bucket === 'week' ? 'bucket-toggle__btn--active' : '']"
          @click="setBucket('week')"
        >Неделя</button>
      </div>
      <v-button primary @click="openCampaignBuilder">
        <v-icon name="campaign" left />
        Создать кампанию
      </v-button>
      <v-button secondary :disabled="!sources.length" @click="exportCsv">
        <v-icon name="download" left />
        Экспорт CSV
      </v-button>
      <v-button secondary :loading="loading" @click="refresh">
        <v-icon name="refresh" left />
        Обновить
      </v-button>
    </template>

    <div class="page">
      <div class="page__main">
        <section class="hero">
          <div class="hero__left">
            <div class="hero__kicker">Vectra Connect</div>
            <h1 class="hero__title">UTM Stats</h1>
            <p class="hero__subtitle">
              Полная воронка по источникам трафика: от первой регистрации до активной подписки и оплаты.
              После PR feat/acquisition-source-attribution цифры включают всю цепочку приглашений
              (друзья пришедших по UTM-ссылкам наследуют тег источника).
            </p>
          </div>

          <div class="hero__right">
            <label class="field field--compact">
              <span>Префикс UTM (сервер)</span>
              <input
                v-model="filters.utm_prefix"
                type="text"
                class="input"
                placeholder="напр. qr_rt_"
                @keyup.enter="refresh"
              />
            </label>
            <label class="field field--compact">
              <span>Поиск (локально)</span>
              <input
                v-model="localSearch"
                type="text"
                class="input"
                placeholder="фильтр по подстроке"
              />
            </label>
            <label class="field field--compact">
              <span>С даты</span>
              <input v-model="filters.since" type="date" class="input" @change="onRangeChange" />
            </label>
            <label class="field field--compact">
              <span>До даты</span>
              <input v-model="filters.until" type="date" class="input" @change="onRangeChange" />
            </label>
            <label class="field field--compact">
              <span>Лимит строк</span>
              <select v-model.number="filters.limit" class="input input--select" @change="refresh">
                <option v-for="opt in limitOptions" :key="opt" :value="opt">{{ opt }}</option>
              </select>
            </label>
          </div>
          <div class="hero__presets">
            <span class="hero__presets-label">Период:</span>
            <button
              v-for="preset in datePresets"
              :key="preset.key"
              type="button"
              :class="['preset-btn', activePreset === preset.key ? 'preset-btn--active' : '']"
              @click="applyPreset(preset.key)"
            >{{ preset.label }}</button>
          </div>
        </section>

        <section v-if="loading" class="state-card">
          <v-progress-circular indeterminate />
          <div class="state-card__title">Загружаем данные…</div>
        </section>
        <section v-else-if="errorMessage" class="state-card state-card--error">
          <v-icon name="error_outline" />
          <div class="state-card__title">{{ errorMessage }}</div>
          <v-button @click="refresh"><v-icon name="refresh" left /> Попробовать снова</v-button>
        </section>

        <template v-else>
          <section class="totals">
            <div class="metric-card">
              <div class="metric-card__label">Всего пользователей</div>
              <div class="metric-card__value">{{ formatNumber(totals.users_total) }}</div>
              <div class="metric-card__hint">в выбранном диапазоне</div>
            </div>
            <div class="metric-card">
              <div class="metric-card__label">С UTM-меткой</div>
              <div class="metric-card__value">{{ formatNumber(totals.users_with_utm) }}</div>
              <div class="metric-card__hint">{{ withUtmPercent }}% от всего</div>
            </div>
            <div class="metric-card">
              <div class="metric-card__label">Без UTM</div>
              <div class="metric-card__value">{{ formatNumber(totals.users_no_utm) }}</div>
              <div class="metric-card__hint">органика и старые юзеры</div>
            </div>
            <div class="metric-card">
              <div class="metric-card__label">Источников</div>
              <div class="metric-card__value">{{ formatNumber(sources.length) }}</div>
              <div class="metric-card__hint">отдельных UTM-меток</div>
            </div>
          </section>

          <section v-if="insights" class="insights">
            <div class="insights__card">
              <div class="insights__label">Регистрации</div>
              <div class="insights__value">{{ formatNumber(insights.registrations) }}</div>
              <div :class="['insights__delta', deltaClass(insights.registrations_delta)]">
                {{ formatDelta(insights.registrations_delta) }}
              </div>
              <div class="insights__hint">vs предыдущий период {{ insights.period_days }}д</div>
            </div>
            <div class="insights__card">
              <div class="insights__label">Платных юзеров</div>
              <div class="insights__value">{{ formatNumber(insights.paid) }}</div>
              <div :class="['insights__delta', deltaClass(insights.paid_delta)]">
                {{ formatDelta(insights.paid_delta) }}
              </div>
              <div class="insights__hint">{{ formatPercent(insights.paid, insights.registrations) }} от регистраций</div>
            </div>
            <div class="insights__card">
              <div class="insights__label">Выручка</div>
              <div class="insights__value">{{ formatRub(insights.revenue) }}</div>
              <div :class="['insights__delta', deltaClass(insights.revenue_delta)]">
                {{ formatDelta(insights.revenue_delta, true) }}
              </div>
              <div class="insights__hint">ARPU {{ formatRub(insights.arpu) }}</div>
            </div>
            <div class="insights__card">
              <div class="insights__label">Доля косвенных</div>
              <div class="insights__value">{{ formatPercent(insights.indirect_users, insights.registrations) }}</div>
              <div class="insights__hint">D {{ formatNumber(insights.direct_users) }} · I {{ formatNumber(insights.indirect_users) }}</div>
            </div>
          </section>

          <section v-if="compareSet.size > 0" class="compare-panel">
            <div class="compare-panel__head">
              <div>
                <strong>Сравнение: {{ compareSet.size }} {{ compareSet.size === 1 ? 'кампания' : 'кампании' }}</strong>
                <span class="compare-panel__hint">Снять чекбоксы или кликнуть «Сбросить», чтобы выйти.</span>
              </div>
              <div class="compare-panel__actions">
                <v-button x-small secondary @click="clearCompare">Сбросить</v-button>
              </div>
            </div>
            <div v-if="compareLoading" class="compare-panel__loading">
              <v-progress-circular indeterminate small />
              <span>Подгружаем серии…</span>
            </div>
            <template v-else>
              <div class="compare-panel__legend">
                <div
                  v-for="(item, idx) in compareSeries"
                  :key="item.utm"
                  class="compare-legend-item"
                  :style="{ '--legend-color': compareColors[idx % compareColors.length] }"
                >
                  <span class="compare-legend-item__swatch"></span>
                  <span class="compare-legend-item__label">{{ item.utm || '— без UTM —' }}</span>
                </div>
              </div>
              <div class="compare-panel__chart" v-html="renderCompareSvg(640, 220)"></div>
              <div class="compare-panel__metric-tabs">
                <button
                  v-for="m in compareMetricOptions"
                  :key="m.key"
                  type="button"
                  :class="['metric-tab', compareMetric === m.key ? 'metric-tab--active' : '']"
                  @click="compareMetric = m.key"
                >{{ m.label }}</button>
              </div>
            </template>
          </section>

          <section v-if="sources.length === 0" class="state-card">
            <v-icon name="inbox" />
            <div class="state-card__title">Нет данных по выбранным фильтрам</div>
            <div class="state-card__hint">Попробуй убрать префикс или сдвинуть дату.</div>
          </section>

          <section v-else class="table-card">
            <div class="table-card__head">
              <div>Источники по конверсии — {{ visibleSources.length }} из {{ sources.length }}</div>
              <div class="table-card__hint">
                <strong>Клик по тегу UTM</strong> — поставить точный фильтр на сервер и подгрузить только эту кампанию.
                <strong>Клик по заголовку</strong> — сортировка по столбцу (↑ asc / ↓ desc / × сброс).
                <strong>Клик по строке</strong> — раскрыть метрики кампании (конверсия по воронке, ARPU, длительность жизни тега).
                В ячейках «Всего», «Активная подписка», «Платных», «Доход» под основной цифрой — сплит на прямых (D) и косвенных (I)
                согласно PR feat/acquisition-source-attribution.
              </div>
            </div>
            <div class="table-wrap">
              <table class="table">
                <thead>
                  <tr>
                    <th class="table__col table__col--check">
                      <span class="th-tooltip" title="Сравнить выбранные">⇄</span>
                    </th>
                    <th
                      v-for="col in columns"
                      :key="col.key"
                      :class="['table__col', col.alignClass, 'table__col--sortable', sort.key === col.key ? 'table__col--sorted' : '']"
                      @click="toggleSort(col.key)"
                    >
                      <span class="sort-head">
                        <span>{{ col.label }}</span>
                        <span class="sort-indicator">{{ sortIndicator(col.key) }}</span>
                      </span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <template v-for="row in visibleSources" :key="row.utm ?? '__no_utm__'">
                    <tr
                      :class="['table__row--clickable', expanded.has(row.utm ?? '__no_utm__') ? 'table__row--expanded' : '']"
                      @click="toggleExpand(row)"
                    >
                      <td class="table__col table__col--check" @click.stop>
                        <input
                          type="checkbox"
                          class="compare-checkbox"
                          :checked="compareSet.has(row.utm ?? '__no_utm__')"
                          :disabled="!compareSet.has(row.utm ?? '__no_utm__') && compareSet.size >= 5"
                          :title="compareSet.size >= 5 && !compareSet.has(row.utm ?? '__no_utm__') ? 'Максимум 5 кампаний для сравнения' : 'Добавить в сравнение'"
                          @change="toggleCompare(row)"
                        />
                      </td>
                      <td class="table__col table__col--utm">
                        <button
                          v-if="row.utm"
                          type="button"
                          class="tag tag--campaign tag--clickable"
                          :title="'Подгрузить только ' + row.utm"
                          @click.stop="setExactFilter(row.utm)"
                        >{{ row.utm }}</button>
                        <span v-else class="tag tag--null">— без UTM —</span>
                      </td>
                      <td class="table__col table__col--num">
                        <div class="cell-stack">
                          <div class="cell-stack__main">{{ formatNumber(row.users_total) }}</div>
                          <div class="cell-stack__split">
                            <span class="split-pill split-pill--direct">D {{ formatNumber(row.users_direct) }}</span>
                            <span class="split-pill split-pill--indirect">I {{ formatNumber(row.users_indirect) }}</span>
                          </div>
                        </div>
                      </td>
                      <td class="table__col table__col--num">{{ formatNumber(row.users_registered) }}</td>
                      <td class="table__col table__col--num">{{ formatNumber(row.users_used_trial) }}</td>
                      <td class="table__col table__col--num">{{ formatNumber(row.users_key_activated) }}</td>
                      <td class="table__col table__col--num">
                        <div class="cell-stack">
                          <div class="cell-stack__main">{{ formatNumber(row.users_active_subscription) }}</div>
                          <div class="cell-stack__split">
                            <span class="split-pill split-pill--direct">D {{ formatNumber(row.users_active_subscription_direct) }}</span>
                            <span class="split-pill split-pill--indirect">I {{ formatNumber(row.users_active_subscription_indirect) }}</span>
                          </div>
                        </div>
                      </td>
                      <td class="table__col table__col--num">
                        <div class="cell-stack">
                          <div class="cell-stack__main">{{ formatNumber(row.users_paid) }}</div>
                          <div class="cell-stack__split">
                            <span class="split-pill split-pill--direct">D {{ formatNumber(row.users_paid_direct) }}</span>
                            <span class="split-pill split-pill--indirect">I {{ formatNumber(row.users_paid_indirect) }}</span>
                          </div>
                        </div>
                      </td>
                      <td class="table__col table__col--num">
                        <div class="cell-stack">
                          <div class="cell-stack__main">{{ formatRub(row.revenue_rub) }}</div>
                          <div class="cell-stack__split">
                            <span class="split-pill split-pill--direct">D {{ formatRub(row.revenue_rub_direct) }}</span>
                            <span class="split-pill split-pill--indirect">I {{ formatRub(row.revenue_rub_indirect) }}</span>
                          </div>
                        </div>
                      </td>
                      <td class="table__col table__col--date">{{ formatDate(row.first_seen) }}</td>
                      <td class="table__col table__col--date">{{ formatDate(row.last_seen) }}</td>
                    </tr>
                    <tr v-if="expanded.has(row.utm ?? '__no_utm__')" class="table__row--detail">
                      <td :colspan="columns.length + 1">
                        <div class="detail-layout">
                          <div class="detail-pane detail-pane--chart">
                            <div class="detail-pane__head">
                              <span class="detail-pane__title">Динамика по {{ bucket === 'day' ? 'дням' : 'неделям' }}</span>
                              <span v-if="detailCache.get(row.utm ?? '__no_utm__')?.timeseries" class="detail-pane__hint">
                                {{ detailCache.get(row.utm ?? '__no_utm__').timeseries.buckets.length }} точек
                              </span>
                            </div>
                            <div v-if="isDetailLoading(row)" class="detail-loading">
                              <v-progress-circular indeterminate small />
                              <span>Подгружаем…</span>
                            </div>
                            <div
                              v-else-if="hasDetail(row)"
                              class="detail-chart"
                              v-html="renderTimeseriesSvg(detailCache.get(row.utm ?? '__no_utm__').timeseries, 520, 200)"
                            ></div>
                            <div v-else class="detail-empty">Нет данных</div>
                          </div>
                          <div class="detail-pane detail-pane--funnel">
                            <div class="detail-pane__head">
                              <span class="detail-pane__title">Воронка</span>
                              <span class="detail-pane__hint">% от total / % от пред. шага</span>
                            </div>
                            <div v-if="isDetailLoading(row)" class="detail-loading">
                              <v-progress-circular indeterminate small />
                              <span>Подгружаем…</span>
                            </div>
                            <div
                              v-else-if="hasDetail(row)"
                              class="detail-funnel"
                              v-html="renderFunnelSvg(detailCache.get(row.utm ?? '__no_utm__').funnel, 360, 240)"
                            ></div>
                            <div v-else class="detail-empty">Нет данных</div>
                          </div>
                        </div>
                        <div class="detail-grid">
                          <div class="detail-card">
                            <div class="detail-card__label">Конверсия в регистрацию</div>
                            <div class="detail-card__value">{{ formatPercent(row.users_registered, row.users_total) }}</div>
                            <div class="detail-card__hint">{{ formatNumber(row.users_registered) }} / {{ formatNumber(row.users_total) }}</div>
                          </div>
                          <div class="detail-card">
                            <div class="detail-card__label">Конверсия в триал</div>
                            <div class="detail-card__value">{{ formatPercent(row.users_used_trial, row.users_total) }}</div>
                            <div class="detail-card__hint">{{ formatNumber(row.users_used_trial) }} / {{ formatNumber(row.users_total) }}</div>
                          </div>
                          <div class="detail-card">
                            <div class="detail-card__label">Конверсия в платных</div>
                            <div class="detail-card__value">{{ formatPercent(row.users_paid, row.users_total) }}</div>
                            <div class="detail-card__hint">{{ formatNumber(row.users_paid) }} / {{ formatNumber(row.users_total) }}</div>
                          </div>
                          <div class="detail-card">
                            <div class="detail-card__label">ARPU (по платным)</div>
                            <div class="detail-card__value">{{ formatRub(arpu(row)) }}</div>
                            <div class="detail-card__hint">{{ formatRub(row.revenue_rub) }} / {{ formatNumber(row.users_paid) }}</div>
                          </div>
                          <div class="detail-card">
                            <div class="detail-card__label">Доля косвенных</div>
                            <div class="detail-card__value">{{ formatPercent(row.users_indirect, row.users_total) }}</div>
                            <div class="detail-card__hint">D {{ formatNumber(row.users_direct) }} · I {{ formatNumber(row.users_indirect) }}</div>
                          </div>
                          <div class="detail-card">
                            <div class="detail-card__label">Длительность жизни тега</div>
                            <div class="detail-card__value">{{ campaignDuration(row) }}</div>
                            <div class="detail-card__hint">{{ formatDate(row.first_seen) }} → {{ formatDate(row.last_seen) }}</div>
                          </div>
                        </div>
                        <div v-if="subTags(row).length" class="sub-tags">
                          <div class="sub-tags__label">Сабтеги в текущей выборке (одного префикса)</div>
                          <div class="sub-tags__list">
                            <button
                              v-for="sub in subTags(row)"
                              :key="sub.utm"
                              type="button"
                              class="tag tag--campaign tag--clickable"
                              :title="'Подгрузить только ' + sub.utm"
                              @click.stop="setExactFilter(sub.utm)"
                            >{{ sub.utm }} · {{ formatNumber(sub.users_total) }}</button>
                          </div>
                        </div>
                      </td>
                    </tr>
                  </template>
                </tbody>
              </table>
            </div>
          </section>

          <section class="footer-meta">
            <div>Сгенерировано: {{ formatDate(generatedAt) }}</div>
            <div v-if="filtersApplied.utm_prefix">Префикс: {{ filtersApplied.utm_prefix }}</div>
            <div v-if="filtersApplied.since">С даты: {{ formatDate(filtersApplied.since) }}</div>
          </section>
        </template>
      </div>
    </div>
  </private-view>

  <!-- ===== Campaign Builder Modal ===== -->
  <div v-if="campaignBuilder.open" class="cb-overlay" @click.self="closeCampaignBuilder">
    <div class="cb-modal" role="dialog" aria-label="Создать кампанию">
      <header class="cb-modal__head">
        <div>
          <div class="cb-modal__kicker">Создать кампанию</div>
          <h2 class="cb-modal__title">Сборка UTM-кампании</h2>
          <p class="cb-modal__sub">
            Сформируй UTM-тег и забери готовый пакет: ссылки для веба и Telegram, QR-коды и
            сниппеты для постов на форумах, в каналах и личных сообщениях.
          </p>
        </div>
        <button type="button" class="cb-modal__close" aria-label="Закрыть" @click="closeCampaignBuilder">×</button>
      </header>

      <section class="cb-section">
        <div class="cb-section__title">UTM-тег</div>
        <div class="cb-form">
          <label class="cb-field cb-field--utm">
            <span class="cb-field__label">Имя тега</span>
            <input
              v-model="campaignBuilder.utm"
              type="text"
              class="cb-input"
              placeholder="напр. qr_rt_launch_2026_05_hero"
              maxlength="50"
              @input="onCampaignUtmInput"
            />
            <span v-if="campaignBuilder.error" class="cb-field__error">{{ campaignBuilder.error }}</span>
            <span v-else class="cb-field__hint">
              Латинские буквы, цифры, нижнее подчёркивание. До 50 символов. Литерал «partner» — зарезервирован.
            </span>
          </label>
          <label class="cb-field">
            <span class="cb-field__label">Подпись (внутренняя)</span>
            <input
              v-model="campaignBuilder.label"
              type="text"
              class="cb-input"
              placeholder="напр. RuTracker · Шапка"
              maxlength="120"
            />
            <span class="cb-field__hint">Используется в текстах сниппетов. Можно оставить пустым.</span>
          </label>
        </div>

        <div class="cb-templates">
          <div class="cb-templates__title">Быстрые шаблоны</div>
          <div class="cb-templates__list">
            <button
              v-for="tpl in campaignTemplates"
              :key="tpl.key"
              type="button"
              class="cb-tpl-btn"
              :title="tpl.hint"
              @click="applyCampaignTemplate(tpl)"
            >{{ tpl.label }}</button>
          </div>
        </div>
      </section>

      <section v-if="!campaignBuilder.error && campaignBuilder.utm" class="cb-section">
        <div class="cb-section__title">Сгенерированные ссылки</div>
        <div class="cb-urls">
          <div v-for="link in campaignLinks" :key="link.key" class="cb-url">
            <div class="cb-url__head">
              <span class="cb-url__icon"><v-icon :name="link.icon" small /></span>
              <span class="cb-url__name">{{ link.name }}</span>
            </div>
            <code class="cb-url__value">{{ link.url }}</code>
            <div class="cb-url__actions">
              <button type="button" class="cb-mini-btn" @click="copy(link.url, link.key)">
                {{ copied === link.key ? 'Скопировано ✓' : 'Копировать' }}
              </button>
              <button type="button" class="cb-mini-btn" @click="downloadQr(link)">QR · SVG</button>
            </div>
            <div class="cb-url__qr" v-html="renderQrSvg(link.url, 140)"></div>
          </div>
        </div>
      </section>

      <section v-if="!campaignBuilder.error && campaignBuilder.utm" class="cb-section">
        <div class="cb-section__title">Сниппеты для постов</div>
        <div class="cb-snips">
          <div v-for="snip in campaignSnippets" :key="snip.key" class="cb-snip">
            <div class="cb-snip__head">
              <span class="cb-snip__name">{{ snip.name }}</span>
              <button type="button" class="cb-mini-btn" @click="copy(snip.body, 'snip_' + snip.key)">
                {{ copied === 'snip_' + snip.key ? 'Скопировано ✓' : 'Копировать' }}
              </button>
            </div>
            <textarea class="cb-snip__body" rows="4" readonly>{{ snip.body }}</textarea>
            <div class="cb-snip__hint">{{ snip.hint }}</div>
          </div>
        </div>
      </section>

      <footer class="cb-modal__foot">
        <div class="cb-foot__hint">
          Пока что кампания не сохраняется в Directus — Phase 2 принесёт коллекцию `utm_campaigns` с историей,
          архивом и заметками. Сейчас ты получаешь готовый пакет ассетов и сразу применяешь тег в дашборд.
        </div>
        <div class="cb-foot__actions">
          <v-button secondary @click="closeCampaignBuilder">Отмена</v-button>
          <v-button
            primary
            :disabled="!!campaignBuilder.error || !campaignBuilder.utm"
            @click="applyCampaignAsFilter"
          >Применить как фильтр</v-button>
        </div>
      </footer>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from "vue";
import { useApi } from "@directus/extensions-sdk";
import qrcodeGenerator from "qrcode-generator";

const api = useApi();

// ===== Campaign builder state =============================================
// Phase 1: in-memory only. No persistence yet — Phase 2 will introduce a
// `utm_campaigns` Directus collection for label/description/notes/history.
// Right now the builder hands the operator a ready asset pack (URLs + QRs +
// snippets) and applies the tag to the dashboard filters on confirm.
const campaignBuilder = reactive({
  open: false,
  utm: "",
  label: "",
  error: "",
});
const copied = ref("");
const RESERVED_UTMS = new Set(["partner"]);
const UTM_RE = /^[A-Za-z0-9_]+$/;

const APP_BASE = "https://app.vectra-pro.net";
const BOT_HANDLE = "VectraConnect_bot";

const campaignTemplates = [
  {
    key: "rt_post_section",
    label: "RuTracker · секция поста",
    hint: "qr_rt_launch_<YYYY_MM>_<секция>",
    apply: () => `qr_rt_launch_${monthStamp()}_section`,
  },
  {
    key: "tg_channel_post",
    label: "Telegram · пост в канале",
    hint: "qr_tg_<канал>_<YYYY_MM_DD>",
    apply: () => `qr_tg_channel_${dateStamp()}`,
  },
  {
    key: "ig_story",
    label: "Instagram · сторис",
    hint: "ig_story_<YYYY_MM_DD>",
    apply: () => `ig_story_${dateStamp()}`,
  },
  {
    key: "google_ads",
    label: "Google Ads · кампания",
    hint: "google_ads_<YYYY_MM>",
    apply: () => `google_ads_${monthStamp()}`,
  },
  {
    key: "youtube_video",
    label: "YouTube · видео",
    hint: "yt_<slug>_<YYYY_MM>",
    apply: () => `yt_video_${monthStamp()}`,
  },
  {
    key: "blogger_outreach",
    label: "Блогер · упоминание",
    hint: "blogger_<ник>_<YYYY_MM>",
    apply: () => `blogger_handle_${monthStamp()}`,
  },
];

function dateStamp() {
  const d = new Date();
  return `${d.getUTCFullYear()}_${String(d.getUTCMonth() + 1).padStart(2, "0")}_${String(d.getUTCDate()).padStart(2, "0")}`;
}

function monthStamp() {
  const d = new Date();
  return `${d.getUTCFullYear()}_${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

function openCampaignBuilder() {
  campaignBuilder.open = true;
  campaignBuilder.utm = "";
  campaignBuilder.label = "";
  campaignBuilder.error = "";
  copied.value = "";
}

function closeCampaignBuilder() {
  campaignBuilder.open = false;
}

function applyCampaignTemplate(tpl) {
  campaignBuilder.utm = tpl.apply();
  campaignBuilder.error = validateUtm(campaignBuilder.utm);
}

function onCampaignUtmInput() {
  campaignBuilder.error = validateUtm(campaignBuilder.utm);
}

function validateUtm(value) {
  const v = (value || "").trim();
  if (!v) return "";
  if (v.length > 50) return "Максимум 50 символов.";
  if (!UTM_RE.test(v)) return "Только латинские буквы, цифры и подчёркивание.";
  if (RESERVED_UTMS.has(v)) return "Литерал «partner» зарезервирован системой — выбери другой.";
  return "";
}

const campaignLinks = computed(() => {
  const utm = campaignBuilder.utm.trim();
  if (!utm) return [];
  return [
    {
      key: "web",
      name: "Веб-кабинет",
      icon: "language",
      url: `${APP_BASE}?start=${utm}`,
    },
    {
      key: "bot",
      name: "Telegram-бот",
      icon: "telegram",
      url: `https://t.me/${BOT_HANDLE}?start=${utm}`,
    },
    {
      key: "miniapp",
      name: "Telegram Mini App",
      icon: "rocket_launch",
      url: `https://t.me/${BOT_HANDLE}/start?startapp=${utm}`,
    },
  ];
});

const campaignSnippets = computed(() => {
  const utm = campaignBuilder.utm.trim();
  if (!utm) return [];
  const label = campaignBuilder.label.trim() || "Vectra Connect";
  const webUrl = `${APP_BASE}?start=${utm}`;
  const botUrl = `https://t.me/${BOT_HANDLE}?start=${utm}`;
  return [
    {
      key: "bbcode",
      name: "BBCode (RuTracker / phpBB)",
      hint: "Вставить в пост на форуме. Промокод RUTRACKER применится автоматически после регистрации.",
      body: [
        `[b]${label}[/b]`,
        `[url=${webUrl}]${APP_BASE}[/url] · [url=${botUrl}]@${BOT_HANDLE}[/url]`,
        "",
        `[i]10 дней бесплатно · работает в РФ · YouTube без рекламы. Промокод [b]RUTRACKER[/b] активирует подписку.[/i]`,
      ].join("\n"),
    },
    {
      key: "markdown",
      name: "Markdown (Telegram, Notion, GitHub)",
      hint: "Подходит для Telegram-каналов с разрешённым Markdown и постов в Notion.",
      body: [
        `**${label}**`,
        `[${APP_BASE}](${webUrl}) · [@${BOT_HANDLE}](${botUrl})`,
        "",
        "10 дней бесплатно · работает в РФ · YouTube без рекламы.",
        "Промокод **RUTRACKER** активирует подписку.",
      ].join("\n"),
    },
    {
      key: "plain",
      name: "Plain text (DM / SMS / любое)",
      hint: "Голый текст без разметки. Подходит для личных сообщений и SMS.",
      body: [
        `${label}`,
        `${webUrl}`,
        `${botUrl}`,
        "",
        "10 дней бесплатно. Промокод RUTRACKER активирует подписку.",
      ].join("\n"),
    },
    {
      key: "html",
      name: "HTML (письмо, сайт)",
      hint: "Готовый <a>-тег для встраивания в HTML-письма или статичные сайты.",
      body:
        `<p><strong>${label}</strong><br />\n` +
        `<a href="${webUrl}">${APP_BASE}</a> · <a href="${botUrl}">@${BOT_HANDLE}</a><br />\n` +
        `10 дней бесплатно. Промокод <code>RUTRACKER</code> активирует подписку.</p>`,
    },
  ];
});

// QR encoder — produces an inline SVG string at any size. ErrorCorrection L
// is enough for short URLs and keeps QR density low (more scannable on
// imperfect prints).
function renderQrSvg(text, size) {
  if (!text) return "";
  try {
    const qr = qrcodeGenerator(0, "M");
    qr.addData(text);
    qr.make();
    const moduleCount = qr.getModuleCount();
    const cell = size / moduleCount;
    const rects = [];
    for (let r = 0; r < moduleCount; r++) {
      for (let c = 0; c < moduleCount; c++) {
        if (qr.isDark(r, c)) {
          const x = (c * cell).toFixed(2);
          const y = (r * cell).toFixed(2);
          const s = cell.toFixed(2);
          rects.push(`<rect x="${x}" y="${y}" width="${s}" height="${s}" fill="#f0f6fc"/>`);
        }
      }
    }
    return `<svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" xmlns="http://www.w3.org/2000/svg" style="background:#0d1117;border-radius:6px;padding:6px;box-sizing:content-box;display:block;">${rects.join("")}</svg>`;
  } catch (err) {
    return `<div class="cb-qr-error">QR не сгенерирован: ${String(err?.message || err)}</div>`;
  }
}

function downloadQr(link) {
  const svg = renderQrSvg(link.url, 320);
  const blob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  const utm = (campaignBuilder.utm || "campaign").replace(/[^a-zA-Z0-9_]/g, "_");
  a.download = `vectra_qr_${utm}_${link.key}.svg`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

async function copy(text, key) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
    }
    copied.value = key;
    setTimeout(() => {
      if (copied.value === key) copied.value = "";
    }, 1800);
  } catch {
    /* swallow — user can still select text manually */
  }
}

function applyCampaignAsFilter() {
  if (campaignBuilder.error || !campaignBuilder.utm) return;
  const utm = campaignBuilder.utm.trim();
  closeCampaignBuilder();
  setExactFilter(utm);
}

const loading = ref(false);
const errorMessage = ref("");
const sources = ref([]);
const totals = reactive({ users_total: 0, users_with_utm: 0, users_no_utm: 0 });
const generatedAt = ref(null);
const filtersApplied = reactive({ utm_prefix: null, since: null, until: null, limit: 200 });

const limitOptions = [50, 100, 200, 500, 1000];

const filters = reactive({
  utm_prefix: "",
  since: "",
  until: "",
  limit: 200,
});

// Period presets — keep semantics consistent with /utm-stats `until` being
// EXCLUSIVE (we add 1 day to the "до даты" so today's "vчера 00:00" up to
// "сегодня 23:59" reads naturally).
const datePresets = [
  { key: "today", label: "Сегодня" },
  { key: "last_7d", label: "7 дней" },
  { key: "last_30d", label: "30 дней" },
  { key: "last_90d", label: "90 дней" },
  { key: "this_month", label: "Этот месяц" },
  { key: "last_month", label: "Прошлый месяц" },
  { key: "all_time", label: "Всё время" },
];
const activePreset = ref("last_30d");
const bucket = ref("day");

// Client-side state: local search + sort + expanded rows + compare set.
const localSearch = ref("");
const sort = reactive({ key: "users_total", dir: "desc" });
const expanded = ref(new Set());
const compareSet = ref(new Set());
const compareMetric = ref("registrations");
const compareMetricOptions = [
  { key: "registrations", label: "Регистрации" },
  { key: "paid_count", label: "Платные" },
  { key: "revenue_rub", label: "Выручка" },
];
const compareSeries = ref([]);
const compareLoading = ref(false);
const compareColors = ["#58a6ff", "#3bc9db", "#b692f6", "#f59f00", "#ff6b6b"];

// Detail cache: { 'utm-key' -> { timeseries, funnel, loading: bool, ts: number } }
const detailCache = ref(new Map());
const DETAIL_TTL_MS = 60_000;

// Columns descriptor drives header rendering AND sort dispatch. `alignClass`
// keeps numeric vs UTM vs date alignment in sync with body cells.
const columns = [
  { key: "utm", label: "UTM", alignClass: "table__col--utm" },
  { key: "users_total", label: "Всего", alignClass: "table__col--num" },
  { key: "users_registered", label: "Регистр.", alignClass: "table__col--num" },
  { key: "users_used_trial", label: "Триал", alignClass: "table__col--num" },
  { key: "users_key_activated", label: "Активация ключа", alignClass: "table__col--num" },
  { key: "users_active_subscription", label: "Активная подписка", alignClass: "table__col--num" },
  { key: "users_paid", label: "Платных", alignClass: "table__col--num" },
  { key: "revenue_rub", label: "Доход, ₽", alignClass: "table__col--num" },
  { key: "first_seen", label: "Первый", alignClass: "table__col--date" },
  { key: "last_seen", label: "Последний", alignClass: "table__col--date" },
];

const withUtmPercent = computed(() => {
  if (!totals.users_total) return 0;
  return Math.round((totals.users_with_utm / totals.users_total) * 100);
});

// Filter (local) -> sort -> render. Filtering and sorting both run client-side
// against the rows fetched by the server prefix filter, so toggling search /
// sort never hits the API.
const visibleSources = computed(() => {
  const needle = localSearch.value.trim().toLowerCase();
  let rows = sources.value;
  if (needle) {
    rows = rows.filter((row) => {
      const utm = (row.utm || "").toLowerCase();
      return utm.includes(needle) || (!row.utm && needle === "null");
    });
  }
  if (!sort.key) return rows;
  const dir = sort.dir === "asc" ? 1 : -1;
  const sortKey = sort.key;
  return [...rows].sort((a, b) => {
    const av = a[sortKey];
    const bv = b[sortKey];
    // null/undefined / utm comparison
    if (av == null && bv == null) return 0;
    if (av == null) return 1; // empty goes last
    if (bv == null) return -1;
    if (typeof av === "number" && typeof bv === "number") return (av - bv) * dir;
    return String(av).localeCompare(String(bv), "ru") * dir;
  });
});

// Sub-tags = rows whose utm starts with current row's utm + "_" or "." or "/".
// Surfaced when expanding a row so the user can drill into per-section
// breakdowns of a campaign family (e.g. qr_rt_launch_2026_05 -> _hero/_about).
function subTags(row) {
  if (!row.utm) return [];
  const base = row.utm;
  return sources.value.filter((other) => {
    if (!other.utm || other.utm === base) return false;
    return other.utm.startsWith(base + "_") || other.utm.startsWith(base + ".") || other.utm.startsWith(base + "/");
  });
}

function toggleSort(key) {
  if (sort.key !== key) {
    sort.key = key;
    // Numeric columns default to desc (biggest channel first); textual to asc.
    const numeric = ["users_total","users_registered","users_used_trial","users_key_activated","users_active_subscription","users_paid","revenue_rub"];
    sort.dir = numeric.includes(key) || key === "first_seen" || key === "last_seen" ? "desc" : "asc";
    return;
  }
  if (sort.dir === "desc") {
    sort.dir = "asc";
  } else if (sort.dir === "asc") {
    sort.key = null;
    sort.dir = "desc";
  }
}

function sortIndicator(key) {
  if (sort.key !== key) return "";
  return sort.dir === "asc" ? "↑" : "↓";
}

function toggleExpand(row) {
  const key = row.utm ?? "__no_utm__";
  const next = new Set(expanded.value);
  if (next.has(key)) {
    next.delete(key);
  } else {
    next.add(key);
    loadDetail(row);
  }
  expanded.value = next;
  syncUrl();
}

function hasDetail(row) {
  const entry = detailCache.value.get(row.utm ?? "__no_utm__");
  return !!(entry && entry.timeseries && entry.funnel && !entry.loading);
}

function isDetailLoading(row) {
  const entry = detailCache.value.get(row.utm ?? "__no_utm__");
  return !!(entry && entry.loading);
}

async function loadDetail(row) {
  const key = row.utm ?? "__no_utm__";
  const existing = detailCache.value.get(key);
  const now = Date.now();
  if (existing && existing.timeseries && now - existing.ts < DETAIL_TTL_MS) return;

  const map = new Map(detailCache.value);
  map.set(key, { ...(existing || {}), loading: true, ts: now });
  detailCache.value = map;

  try {
    const params = {
      utm: key,
      bucket: bucket.value,
    };
    if (filters.since) params.since = filters.since;
    if (filters.until) params.until = exclusiveUntil(filters.until);
    const [tsResp, funResp] = await Promise.all([
      api.get("/admin-widgets/utm-stats/timeseries", { params }),
      api.get("/admin-widgets/utm-stats/funnel", { params }),
    ]);
    const map2 = new Map(detailCache.value);
    map2.set(key, {
      timeseries: tsResp?.data ?? { buckets: [] },
      funnel: funResp?.data ?? { steps: [] },
      loading: false,
      ts: Date.now(),
    });
    detailCache.value = map2;
  } catch (err) {
    const map2 = new Map(detailCache.value);
    map2.set(key, { loading: false, ts: Date.now(), error: err?.message ?? "load failed" });
    detailCache.value = map2;
  }
}

function toggleCompare(row) {
  const key = row.utm ?? "__no_utm__";
  const next = new Set(compareSet.value);
  if (next.has(key)) {
    next.delete(key);
  } else {
    if (next.size >= 5) return;
    next.add(key);
  }
  compareSet.value = next;
  refreshCompareSeries();
  syncUrl();
}

function clearCompare() {
  compareSet.value = new Set();
  compareSeries.value = [];
  syncUrl();
}

async function refreshCompareSeries() {
  if (compareSet.value.size === 0) {
    compareSeries.value = [];
    return;
  }
  compareLoading.value = true;
  try {
    const items = await Promise.all(
      Array.from(compareSet.value).map(async (key) => {
        const params = { utm: key, bucket: bucket.value };
        if (filters.since) params.since = filters.since;
        if (filters.until) params.until = exclusiveUntil(filters.until);
        const resp = await api.get("/admin-widgets/utm-stats/timeseries", { params });
        return resp?.data ?? { utm: key, buckets: [] };
      })
    );
    compareSeries.value = items;
  } catch (err) {
    compareSeries.value = [];
  } finally {
    compareLoading.value = false;
  }
}

watch(compareMetric, () => {
  // re-render only — series already loaded
});

function setBucket(value) {
  if (bucket.value === value) return;
  bucket.value = value;
  // Invalidate detail cache so the open rows reload at the new granularity.
  detailCache.value = new Map();
  // Reload chart data for currently expanded rows.
  for (const key of expanded.value) {
    const src = sources.value.find((s) => (s.utm ?? "__no_utm__") === key);
    if (src) loadDetail(src);
  }
  refreshCompareSeries();
  syncUrl();
}

// Period preset → since/until ISO dates (yyyy-mm-dd).
function applyPreset(key) {
  activePreset.value = key;
  const today = new Date();
  const isoDate = (d) => d.toISOString().slice(0, 10);
  const subDays = (d, n) => {
    const r = new Date(d);
    r.setDate(r.getDate() - n);
    return r;
  };
  switch (key) {
    case "today":
      filters.since = isoDate(today);
      filters.until = isoDate(today);
      break;
    case "last_7d":
      filters.since = isoDate(subDays(today, 7));
      filters.until = isoDate(today);
      break;
    case "last_30d":
      filters.since = isoDate(subDays(today, 30));
      filters.until = isoDate(today);
      break;
    case "last_90d":
      filters.since = isoDate(subDays(today, 90));
      filters.until = isoDate(today);
      break;
    case "this_month": {
      const start = new Date(today.getFullYear(), today.getMonth(), 1);
      filters.since = isoDate(start);
      filters.until = isoDate(today);
      break;
    }
    case "last_month": {
      const start = new Date(today.getFullYear(), today.getMonth() - 1, 1);
      const end = new Date(today.getFullYear(), today.getMonth(), 0);
      filters.since = isoDate(start);
      filters.until = isoDate(end);
      break;
    }
    case "all_time":
      filters.since = "";
      filters.until = "";
      break;
  }
  detailCache.value = new Map();
  refresh();
  refreshCompareSeries();
  syncUrl();
}

function onRangeChange() {
  // User edited a date manually — drop the active preset.
  activePreset.value = "custom";
  detailCache.value = new Map();
  refresh();
  refreshCompareSeries();
  syncUrl();
}

// Convert "до даты" date string (inclusive in user's mind) → exclusive ISO
// upper bound: end of that day + 1ms. `?until=` in the backend uses
// `created_at < until` so we add 1 day.
function exclusiveUntil(yyyyMmDd) {
  if (!yyyyMmDd) return "";
  try {
    const d = new Date(yyyyMmDd + "T00:00:00Z");
    d.setUTCDate(d.getUTCDate() + 1);
    return d.toISOString().slice(0, 10);
  } catch {
    return yyyyMmDd;
  }
}

function setExactFilter(utm) {
  if (!utm) return;
  filters.utm_prefix = utm;
  expanded.value = new Set();
  refresh();
}

function arpu(row) {
  if (!row.users_paid || row.users_paid <= 0) return 0;
  return (row.revenue_rub || 0) / row.users_paid;
}

function campaignDuration(row) {
  if (!row.first_seen || !row.last_seen) return "—";
  try {
    const start = new Date(row.first_seen);
    const end = new Date(row.last_seen);
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return "—";
    const days = Math.round((end - start) / 86400000);
    if (days <= 0) return "в течение одного дня";
    if (days === 1) return "1 день";
    if (days < 5) return `${days} дня`;
    return `${days} дней`;
  } catch {
    return "—";
  }
}

function formatNumber(value) {
  if (value === null || value === undefined) return "—";
  return Number(value).toLocaleString("ru-RU");
}

function formatRub(value) {
  if (value === null || value === undefined) return "—";
  return `${Math.round(Number(value)).toLocaleString("ru-RU")} ₽`;
}

function formatPercent(part, whole) {
  const w = Number(whole);
  if (!w || w <= 0) return "—";
  const p = Number(part) || 0;
  const pct = (p / w) * 100;
  if (pct >= 10) return `${pct.toFixed(1)}%`;
  return `${pct.toFixed(2)}%`;
}

function formatDate(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleString("ru-RU", { dateStyle: "medium", timeStyle: "short" });
  } catch {
    return "—";
  }
}

function csvEscape(value) {
  if (value === null || value === undefined) return "";
  const s = String(value);
  if (s.includes(",") || s.includes('"') || s.includes("\n")) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

function exportCsv() {
  if (!visibleSources.value.length) return;
  const header = [
    "utm","users_total","users_direct","users_indirect",
    "users_registered","users_used_trial","users_key_activated",
    "users_active_subscription","users_active_subscription_direct","users_active_subscription_indirect",
    "users_paid","users_paid_direct","users_paid_indirect",
    "revenue_rub","revenue_rub_direct","revenue_rub_indirect",
    "first_seen","last_seen",
  ];
  const body = visibleSources.value.map((row) =>
    header.map((key) => csvEscape(row[key] ?? (key === "utm" ? "" : 0))).join(",")
  );
  const csv = [header.join(","), ...body].join("\n");
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  const stamp = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, "");
  const prefixPart = (filters.utm_prefix || "all").replace(/[^a-zA-Z0-9_]/g, "_");
  a.download = `utm-stats_${prefixPart}_${stamp}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

async function refresh() {
  loading.value = true;
  errorMessage.value = "";
  try {
    const params = {};
    const trimmedPrefix = (filters.utm_prefix || "").trim();
    if (trimmedPrefix) params.utm_prefix = trimmedPrefix;
    const trimmedSince = (filters.since || "").trim();
    if (trimmedSince) params.since = trimmedSince;
    const trimmedUntil = (filters.until || "").trim();
    if (trimmedUntil) params.until = exclusiveUntil(trimmedUntil);
    if (filters.limit) params.limit = filters.limit;

    const [resp, prevTotals] = await Promise.all([
      api.get("/admin-widgets/utm-stats", { params }),
      // Previous-period totals for the insights delta. Same length window
      // immediately preceding the current one. We only need a single
      // aggregate, so we fetch the same endpoint with a shifted range.
      loadPreviousPeriodTotals(params),
    ]);
    const data = resp?.data ?? {};
    sources.value = Array.isArray(data.sources) ? data.sources : [];
    Object.assign(totals, data.totals ?? {});
    Object.assign(filtersApplied, data.filters_applied ?? {});
    generatedAt.value = data.generated_at ?? null;
    previousTotals.value = prevTotals;
    detailCache.value = new Map();
  } catch (err) {
    errorMessage.value = err?.response?.data?.error || err?.message || "Не удалось загрузить статистику";
    sources.value = [];
  } finally {
    loading.value = false;
  }
}

const previousTotals = ref(null);

async function loadPreviousPeriodTotals(currentParams) {
  if (!currentParams.since || !currentParams.until) return null;
  try {
    const sinceD = new Date(currentParams.since + "T00:00:00Z");
    const untilD = new Date(currentParams.until + "T00:00:00Z");
    const lenMs = untilD - sinceD;
    if (lenMs <= 0) return null;
    const prevUntil = sinceD;
    const prevSince = new Date(sinceD.getTime() - lenMs);
    const params = {
      ...currentParams,
      since: prevSince.toISOString().slice(0, 10),
      until: prevUntil.toISOString().slice(0, 10),
      limit: 1000,
    };
    const resp = await api.get("/admin-widgets/utm-stats", { params });
    const data = resp?.data ?? {};
    const rows = Array.isArray(data.sources) ? data.sources : [];
    return aggregateRows(rows);
  } catch {
    return null;
  }
}

function aggregateRows(rows) {
  return rows.reduce(
    (acc, r) => ({
      registrations: acc.registrations + Number(r.users_registered || 0),
      paid: acc.paid + Number(r.users_paid || 0),
      revenue: acc.revenue + Number(r.revenue_rub || 0),
      direct_users: acc.direct_users + Number(r.users_direct || 0),
      indirect_users: acc.indirect_users + Number(r.users_indirect || 0),
    }),
    { registrations: 0, paid: 0, revenue: 0, direct_users: 0, indirect_users: 0 }
  );
}

const insights = computed(() => {
  const agg = aggregateRows(sources.value);
  const prev = previousTotals.value;
  const periodDays = (() => {
    if (!filters.since || !filters.until) return null;
    const a = new Date(filters.since + "T00:00:00Z");
    const b = new Date(filters.until + "T00:00:00Z");
    return Math.max(1, Math.round((b - a) / 86400000) + 1);
  })();
  const delta = (curr, prev) => {
    if (prev == null) return null;
    if (prev === 0) return curr > 0 ? 1 : 0;
    return (curr - prev) / prev;
  };
  return {
    registrations: agg.registrations,
    paid: agg.paid,
    revenue: agg.revenue,
    arpu: agg.paid > 0 ? agg.revenue / agg.paid : 0,
    direct_users: agg.direct_users,
    indirect_users: agg.indirect_users,
    period_days: periodDays,
    registrations_delta: delta(agg.registrations, prev?.registrations),
    paid_delta: delta(agg.paid, prev?.paid),
    revenue_delta: delta(agg.revenue, prev?.revenue),
  };
});

function deltaClass(d) {
  if (d == null) return "insights__delta--neutral";
  if (d > 0.005) return "insights__delta--up";
  if (d < -0.005) return "insights__delta--down";
  return "insights__delta--neutral";
}

function formatDelta(d, isMoney) {
  if (d == null) return "—";
  const pct = (d * 100).toFixed(1);
  const sign = d > 0 ? "+" : "";
  return `${sign}${pct}%`;
}

// ===== SVG renderers — no external libs ==================================
// Build a small line chart for one timeseries object. Returns a raw SVG
// string injected via v-html. We render three lines: registrations, paid,
// revenue (normalized to its own axis on the right).
function renderTimeseriesSvg(ts, w, h) {
  const buckets = ts?.buckets ?? [];
  const padL = 36, padR = 36, padT = 14, padB = 26;
  const innerW = w - padL - padR;
  const innerH = h - padT - padB;
  if (!buckets.length) {
    return `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}"><text x="${w/2}" y="${h/2}" text-anchor="middle" fill="#6e7681" font-family="Inter, sans-serif" font-size="13">Нет точек</text></svg>`;
  }
  const maxReg = Math.max(1, ...buckets.map((b) => b.registrations));
  const maxPaid = Math.max(1, ...buckets.map((b) => b.paid_count));
  const maxRev = Math.max(1, ...buckets.map((b) => b.revenue_rub));
  const maxLeft = Math.max(maxReg, maxPaid);
  const x = (i) => padL + (buckets.length === 1 ? innerW / 2 : (i * innerW) / (buckets.length - 1));
  const yLeft = (v) => padT + innerH - (v / maxLeft) * innerH;
  const yRight = (v) => padT + innerH - (v / maxRev) * innerH;
  const seriesPath = (vals, scaler) =>
    vals.map((v, i) => (i === 0 ? `M ${x(i)} ${scaler(v)}` : `L ${x(i)} ${scaler(v)}`)).join(" ");

  const regPath = seriesPath(buckets.map((b) => b.registrations), yLeft);
  const paidPath = seriesPath(buckets.map((b) => b.paid_count), yLeft);
  const revPath = seriesPath(buckets.map((b) => b.revenue_rub), yRight);

  const axisTicksLeft = 4;
  const axisY = [];
  for (let i = 0; i <= axisTicksLeft; i++) {
    const val = Math.round((maxLeft * i) / axisTicksLeft);
    const yy = padT + innerH - (i / axisTicksLeft) * innerH;
    axisY.push(`<line x1="${padL}" y1="${yy}" x2="${w - padR}" y2="${yy}" stroke="rgba(110,118,129,0.12)" stroke-width="1" />`);
    axisY.push(`<text x="${padL - 6}" y="${yy + 4}" text-anchor="end" fill="#6e7681" font-family="Inter, sans-serif" font-size="10">${val}</text>`);
  }
  // X labels: first, mid, last
  const labelIdx = buckets.length <= 1 ? [0] : [0, Math.floor(buckets.length / 2), buckets.length - 1];
  const xLabels = labelIdx
    .map((i) => {
      const d = buckets[i]?.bucket_ts ? new Date(buckets[i].bucket_ts) : null;
      const text = d ? `${String(d.getUTCDate()).padStart(2, "0")}.${String(d.getUTCMonth() + 1).padStart(2, "0")}` : "";
      return `<text x="${x(i)}" y="${h - padB + 14}" text-anchor="middle" fill="#6e7681" font-family="Inter, sans-serif" font-size="10">${text}</text>`;
    })
    .join("");

  return `
<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" style="overflow: visible;">
  ${axisY.join("")}
  <path d="${regPath}" stroke="#3bc9db" stroke-width="2" fill="none" stroke-linejoin="round" stroke-linecap="round" />
  <path d="${paidPath}" stroke="#b692f6" stroke-width="2" fill="none" stroke-dasharray="4 3" stroke-linejoin="round" stroke-linecap="round" />
  <path d="${revPath}" stroke="#f59f00" stroke-width="2" fill="none" stroke-dasharray="1 4" stroke-linejoin="round" stroke-linecap="round" />
  ${xLabels}
  <g font-family="Inter, sans-serif" font-size="11" font-weight="600">
    <rect x="${padL}" y="${padT - 8}" width="190" height="0" fill="transparent"/>
    <circle cx="${padL + 6}" cy="${padT}" r="3" fill="#3bc9db"/>
    <text x="${padL + 14}" y="${padT + 3}" fill="#c9d1d9">Регистрации</text>
    <circle cx="${padL + 95}" cy="${padT}" r="3" fill="#b692f6"/>
    <text x="${padL + 103}" y="${padT + 3}" fill="#c9d1d9">Платные</text>
    <circle cx="${padL + 170}" cy="${padT}" r="3" fill="#f59f00"/>
    <text x="${padL + 178}" y="${padT + 3}" fill="#c9d1d9">Выручка</text>
  </g>
</svg>`;
}

// Funnel rendering — vertical stack of bars, width proportional to count
// from the previous step. Each bar shows label + count + ratio_prev + ratio_total.
function renderFunnelSvg(funnel, w, h) {
  const steps = funnel?.steps ?? [];
  if (!steps.length) {
    return `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}"><text x="${w/2}" y="${h/2}" text-anchor="middle" fill="#6e7681" font-family="Inter, sans-serif" font-size="13">Нет данных</text></svg>`;
  }
  const padL = 12, padR = 12;
  const innerW = w - padL - padR;
  const total = steps[0].count || 1;
  const rowH = (h - 8) / steps.length;
  const colorScale = ["#3bc9db", "#66d9e8", "#74c0fc", "#7950f2", "#b692f6", "#f06595"];
  const bars = steps.map((s, i) => {
    const fraction = total > 0 ? s.count / total : 0;
    const barW = Math.max(8, fraction * innerW);
    const barX = padL + (innerW - barW) / 2;
    const y = i * rowH;
    const ratioPrev = i === 0 ? 1 : s.ratio_prev || 0;
    const ratioTotal = s.ratio_total || 0;
    const labelText = s.label || s.key;
    const countText = (s.count || 0).toLocaleString("ru-RU");
    const subText = i === 0 ? "" : ` · ${(ratioPrev * 100).toFixed(1)}% от пред · ${(ratioTotal * 100).toFixed(1)}% от total`;
    return `
      <rect x="${barX}" y="${y + 4}" width="${barW}" height="${rowH - 12}" rx="4" fill="${colorScale[i % colorScale.length]}" opacity="0.85"/>
      <text x="${padL + 4}" y="${y + 18}" fill="#c9d1d9" font-family="Inter, sans-serif" font-size="11" font-weight="700">${labelText}</text>
      <text x="${w - padR - 4}" y="${y + 18}" text-anchor="end" fill="#0d1117" font-family="Inter, sans-serif" font-size="11" font-weight="800">${countText}</text>
      <text x="${padL + 4}" y="${y + rowH - 4}" fill="rgba(201, 209, 217, 0.55)" font-family="Inter, sans-serif" font-size="9.5">${subText}</text>
    `;
  });
  return `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" style="overflow: visible;">${bars.join("")}</svg>`;
}

// Compare overlay chart — multi-series line on a single axis. Metric chosen
// by `compareMetric`. Colors come from `compareColors` indexed by series.
function renderCompareSvg(w, h) {
  const series = compareSeries.value;
  const padL = 40, padR = 16, padT = 12, padB = 28;
  const innerW = w - padL - padR;
  const innerH = h - padT - padB;
  if (!series.length) {
    return `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}"><text x="${w/2}" y="${h/2}" text-anchor="middle" fill="#6e7681" font-family="Inter, sans-serif" font-size="13">Подгрузка серий…</text></svg>`;
  }
  const metric = compareMetric.value;
  // Compute global x axis as union of all timestamps, sorted.
  const allTs = new Set();
  for (const s of series) {
    for (const b of s.buckets ?? []) {
      if (b.bucket_ts) allTs.add(b.bucket_ts);
    }
  }
  const xs = Array.from(allTs).sort();
  if (xs.length === 0) {
    return `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}"><text x="${w/2}" y="${h/2}" text-anchor="middle" fill="#6e7681" font-family="Inter, sans-serif" font-size="13">Нет точек в выбранном диапазоне</text></svg>`;
  }
  const tsIdx = new Map(xs.map((t, i) => [t, i]));

  // Build value matrix per series, aligned to xs (gaps filled with 0).
  const matrix = series.map((s) => {
    const arr = new Array(xs.length).fill(0);
    for (const b of s.buckets ?? []) {
      const i = tsIdx.get(b.bucket_ts);
      if (i != null) arr[i] = Number(b[metric] || 0);
    }
    return arr;
  });
  const max = Math.max(1, ...matrix.flat());
  const x = (i) => padL + (xs.length === 1 ? innerW / 2 : (i * innerW) / (xs.length - 1));
  const y = (v) => padT + innerH - (v / max) * innerH;

  const lines = matrix.map((vals, idx) => {
    const color = compareColors[idx % compareColors.length];
    const path = vals.map((v, i) => (i === 0 ? `M ${x(i)} ${y(v)}` : `L ${x(i)} ${y(v)}`)).join(" ");
    return `<path d="${path}" stroke="${color}" stroke-width="2" fill="none" stroke-linejoin="round" stroke-linecap="round" />`;
  });

  const axisY = [];
  for (let i = 0; i <= 4; i++) {
    const val = Math.round((max * i) / 4);
    const yy = padT + innerH - (i / 4) * innerH;
    axisY.push(`<line x1="${padL}" y1="${yy}" x2="${w - padR}" y2="${yy}" stroke="rgba(110,118,129,0.12)" stroke-width="1" />`);
    axisY.push(`<text x="${padL - 6}" y="${yy + 4}" text-anchor="end" fill="#6e7681" font-family="Inter, sans-serif" font-size="10">${val}</text>`);
  }
  const labelIdx = xs.length <= 1 ? [0] : [0, Math.floor(xs.length / 2), xs.length - 1];
  const xLabels = labelIdx
    .map((i) => {
      const d = new Date(xs[i]);
      const text = `${String(d.getUTCDate()).padStart(2, "0")}.${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
      return `<text x="${x(i)}" y="${h - padB + 14}" text-anchor="middle" fill="#6e7681" font-family="Inter, sans-serif" font-size="10">${text}</text>`;
    })
    .join("");
  return `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" style="overflow: visible;">${axisY.join("")}${lines.join("")}${xLabels}</svg>`;
}

// ===== URL state sync =====================================================
// Encode the relevant filters/state into the URL hash so views are bookmarkable
// and shareable. Reads on mount and writes after any change.
function readUrlState() {
  try {
    const hash = window.location.hash || "";
    const q = hash.startsWith("#") ? hash.slice(1) : hash;
    if (!q.includes("=")) return;
    const params = new URLSearchParams(q.split("?").pop());
    if (params.has("prefix")) filters.utm_prefix = params.get("prefix");
    if (params.has("since")) filters.since = params.get("since");
    if (params.has("until")) filters.until = params.get("until");
    if (params.has("preset")) activePreset.value = params.get("preset");
    if (params.has("bucket") && (params.get("bucket") === "day" || params.get("bucket") === "week")) {
      bucket.value = params.get("bucket");
    }
    if (params.has("limit")) {
      const n = Number(params.get("limit"));
      if (Number.isFinite(n) && n > 0) filters.limit = n;
    }
    if (params.has("search")) localSearch.value = params.get("search");
    if (params.has("sort")) {
      const [k, d] = params.get("sort").split(":");
      if (k) { sort.key = k; sort.dir = d === "asc" ? "asc" : "desc"; }
    }
    if (params.has("compare")) {
      compareSet.value = new Set(params.get("compare").split(",").filter(Boolean));
    }
    if (params.has("expand")) {
      expanded.value = new Set(params.get("expand").split(",").filter(Boolean));
    }
  } catch {}
}

let urlSyncTimer = null;
function syncUrl() {
  if (urlSyncTimer) clearTimeout(urlSyncTimer);
  urlSyncTimer = setTimeout(() => {
    try {
      const params = new URLSearchParams();
      if (filters.utm_prefix) params.set("prefix", filters.utm_prefix);
      if (filters.since) params.set("since", filters.since);
      if (filters.until) params.set("until", filters.until);
      if (activePreset.value && activePreset.value !== "last_30d") params.set("preset", activePreset.value);
      if (bucket.value !== "day") params.set("bucket", bucket.value);
      if (filters.limit && filters.limit !== 200) params.set("limit", String(filters.limit));
      if (localSearch.value) params.set("search", localSearch.value);
      if (sort.key && (sort.key !== "users_total" || sort.dir !== "desc")) params.set("sort", `${sort.key}:${sort.dir}`);
      if (compareSet.value.size) params.set("compare", Array.from(compareSet.value).join(","));
      if (expanded.value.size) params.set("expand", Array.from(expanded.value).join(","));
      const qs = params.toString();
      const base = window.location.pathname + window.location.search;
      window.history.replaceState(null, "", qs ? `${base}#${qs}` : base);
    } catch {}
  }, 200);
}

watch(() => sort.key, syncUrl);
watch(() => sort.dir, syncUrl);
watch(localSearch, syncUrl);
watch(() => filters.utm_prefix, syncUrl);

onMounted(() => {
  readUrlState();
  // Apply default preset if none in URL.
  if (!filters.since && !filters.until && activePreset.value === "last_30d") {
    applyPreset("last_30d");
  } else {
    refresh();
    if (compareSet.value.size > 0) refreshCompareSeries();
  }
});
</script>

<style scoped>
:root,
.page,
.hero,
.metric-card,
.table-card,
.state-card {
  color-scheme: dark;
}

.page {
  padding: 32px 40px 80px;
  background: #0d1117;
  min-height: 100vh;
}

.page__main {
  display: flex;
  flex-direction: column;
  gap: 28px;
  max-width: 1480px;
  margin: 0 auto;
}

.nav {
  padding: 18px 16px;
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.nav__brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-bottom: 18px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.nav__brand-logo {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border-radius: 10px;
  background: linear-gradient(135deg, #1f6feb, #8957e5);
  color: #fff;
  font-weight: 700;
  font-size: 12px;
  letter-spacing: 0.04em;
}

.nav__brand-title {
  color: #f0f6fc;
  font-weight: 600;
  font-size: 15px;
}

.nav__brand-subtitle {
  color: #8b949e;
  font-size: 12px;
  margin-top: 2px;
}

.nav__section-title {
  text-transform: uppercase;
  font-size: 11px;
  letter-spacing: 0.08em;
  color: #6e7681;
  margin-bottom: 8px;
}

.nav__item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  color: #c9d1d9;
  text-decoration: none;
  transition: background 120ms ease;
}

.nav__item:hover {
  background: rgba(255, 255, 255, 0.04);
}

.nav__item--active {
  background: rgba(31, 111, 235, 0.15);
  color: #f0f6fc;
}

.nav__item-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(280px, auto);
  gap: 32px;
  padding: 28px 32px;
  background: linear-gradient(180deg, rgba(31, 111, 235, 0.08), rgba(31, 111, 235, 0));
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 16px;
  align-items: center;
}

.hero__kicker {
  font-size: 12px;
  color: #58a6ff;
  text-transform: uppercase;
  letter-spacing: 0.12em;
}

.hero__title {
  margin: 4px 0 12px;
  font-size: 28px;
  font-weight: 700;
  color: #f0f6fc;
  letter-spacing: -0.01em;
}

.hero__subtitle {
  color: #8b949e;
  font-size: 14px;
  line-height: 1.5;
  max-width: 720px;
}

.hero__right {
  display: grid;
  gap: 12px;
}

.field {
  display: grid;
  gap: 6px;
  font-size: 12px;
  color: #8b949e;
}

.field--compact span {
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.input,
.input--select {
  background: #0d1117;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  padding: 8px 10px;
  color: #f0f6fc;
  font-size: 14px;
  font-family: inherit;
}

.input:focus,
.input--select:focus {
  outline: none;
  border-color: #58a6ff;
  box-shadow: 0 0 0 3px rgba(88, 166, 255, 0.18);
}

.totals {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px;
}

.metric-card {
  padding: 20px 22px;
  background: #161b22;
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.metric-card__label {
  font-size: 12px;
  color: #8b949e;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.metric-card__value {
  font-size: 28px;
  font-weight: 700;
  color: #f0f6fc;
  letter-spacing: -0.01em;
}

.metric-card__hint {
  font-size: 12px;
  color: #6e7681;
}

.table-card {
  background: #161b22;
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 14px;
  overflow: hidden;
}

.table-card__head {
  padding: 18px 22px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  color: #f0f6fc;
  font-weight: 600;
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}

.table-card__hint {
  font-size: 12px;
  color: #6e7681;
  font-weight: 400;
}

.table-wrap {
  overflow-x: auto;
}

.table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.table th,
.table td {
  padding: 12px 14px;
  text-align: left;
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
}

.table th {
  color: #8b949e;
  font-weight: 500;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  background: #0d1117;
  position: sticky;
  top: 0;
}

.table tbody tr:hover {
  background: rgba(31, 111, 235, 0.05);
}

.table__col--num {
  text-align: right;
  font-variant-numeric: tabular-nums;
  color: #f0f6fc;
}

.table__col--date {
  white-space: nowrap;
  color: #8b949e;
}

.cell-stack {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
}

.cell-stack__main {
  font-weight: 600;
  color: #f0f6fc;
}

.cell-stack__split {
  display: flex;
  gap: 4px;
  flex-wrap: nowrap;
  white-space: nowrap;
}

.split-pill {
  display: inline-flex;
  align-items: center;
  padding: 1px 6px;
  border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: -0.01em;
  line-height: 1.2;
}

.split-pill--direct {
  background: rgba(59, 201, 219, 0.12);
  color: #66d9e8;
  border: 1px solid rgba(59, 201, 219, 0.22);
}

.split-pill--indirect {
  background: rgba(151, 117, 250, 0.12);
  color: #b692f6;
  border: 1px solid rgba(151, 117, 250, 0.22);
}

.tag {
  display: inline-flex;
  align-items: center;
  padding: 4px 10px;
  border-radius: 999px;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 12px;
}

.tag--campaign {
  background: rgba(88, 166, 255, 0.12);
  color: #58a6ff;
  border: 1px solid rgba(88, 166, 255, 0.25);
}

.tag--null {
  background: rgba(110, 118, 129, 0.12);
  color: #6e7681;
  border: 1px solid rgba(110, 118, 129, 0.25);
}

.tag--clickable {
  cursor: pointer;
  font: inherit;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 12px;
  line-height: 1.2;
  transition: background 0.15s, color 0.15s, border-color 0.15s, transform 0.05s;
}

.tag--clickable:hover {
  background: rgba(88, 166, 255, 0.22);
  color: #79b8ff;
  border-color: rgba(88, 166, 255, 0.45);
}

.tag--clickable:active {
  transform: translateY(1px);
}

.table__col--sortable {
  cursor: pointer;
  user-select: none;
  transition: background 0.12s, color 0.12s;
}

.table__col--sortable:hover {
  background: rgba(88, 166, 255, 0.05);
  color: #79b8ff;
}

.table__col--sorted {
  color: #58a6ff;
}

.sort-head {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.sort-indicator {
  min-width: 10px;
  text-align: center;
  font-weight: 700;
  color: #58a6ff;
}

.table__row--clickable {
  cursor: pointer;
  transition: background 0.12s;
}

.table__row--expanded {
  background: rgba(88, 166, 255, 0.04);
}

.table__row--detail > td {
  background: rgba(13, 17, 23, 0.55);
  border-top: 0;
  padding: 18px 22px 22px;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 12px;
}

.detail-card {
  padding: 12px 14px;
  border-radius: 10px;
  border: 1px solid rgba(110, 118, 129, 0.18);
  background: linear-gradient(135deg, rgba(33, 38, 45, 0.55), rgba(13, 17, 23, 0.55));
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.detail-card__label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: rgba(201, 209, 217, 0.62);
  font-weight: 700;
}

.detail-card__value {
  font-size: 18px;
  font-weight: 700;
  color: #f0f6fc;
  font-variant-numeric: tabular-nums;
}

.detail-card__hint {
  font-size: 11.5px;
  color: rgba(201, 209, 217, 0.55);
}

.sub-tags {
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px dashed rgba(110, 118, 129, 0.2);
}

.sub-tags__label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: rgba(201, 209, 217, 0.62);
  font-weight: 700;
  margin-bottom: 8px;
}

.sub-tags__list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

/* ===== Date presets ===== */
.hero__presets {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px dashed rgba(110, 118, 129, 0.18);
}

.hero__presets-label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-weight: 700;
  color: rgba(201, 209, 217, 0.62);
  margin-right: 4px;
}

.preset-btn {
  font: inherit;
  font-size: 12px;
  padding: 5px 12px;
  border-radius: 999px;
  border: 1px solid rgba(110, 118, 129, 0.25);
  background: rgba(33, 38, 45, 0.55);
  color: #c9d1d9;
  cursor: pointer;
  transition: background 0.12s, border-color 0.12s, color 0.12s;
}

.preset-btn:hover {
  background: rgba(88, 166, 255, 0.08);
  color: #79b8ff;
  border-color: rgba(88, 166, 255, 0.35);
}

.preset-btn--active {
  background: rgba(88, 166, 255, 0.18);
  color: #79b8ff;
  border-color: rgba(88, 166, 255, 0.55);
  font-weight: 700;
}

/* ===== Bucket toggle (in actions) ===== */
.bucket-toggle {
  display: inline-flex;
  border: 1px solid rgba(110, 118, 129, 0.25);
  border-radius: 8px;
  overflow: hidden;
  height: 32px;
  margin-right: 4px;
}

.bucket-toggle__btn {
  font: inherit;
  padding: 0 12px;
  border: 0;
  background: transparent;
  color: #c9d1d9;
  cursor: pointer;
  font-size: 12px;
  font-weight: 600;
  transition: background 0.12s, color 0.12s;
}

.bucket-toggle__btn:hover {
  background: rgba(88, 166, 255, 0.08);
  color: #79b8ff;
}

.bucket-toggle__btn--active {
  background: rgba(88, 166, 255, 0.18);
  color: #79b8ff;
}

/* ===== Insights summary ===== */
.insights {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}

.insights__card {
  padding: 16px 18px;
  border-radius: 12px;
  border: 1px solid rgba(110, 118, 129, 0.18);
  background: linear-gradient(135deg, rgba(33, 38, 45, 0.65), rgba(13, 17, 23, 0.65));
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.insights__label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-weight: 700;
  color: rgba(201, 209, 217, 0.62);
}

.insights__value {
  font-size: 24px;
  font-weight: 800;
  color: #f0f6fc;
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
}

.insights__delta {
  font-size: 12px;
  font-weight: 700;
  margin-top: 2px;
}

.insights__delta--up { color: #51cf66; }
.insights__delta--down { color: #ff6b6b; }
.insights__delta--neutral { color: rgba(201, 209, 217, 0.55); }

.insights__hint {
  font-size: 11.5px;
  color: rgba(201, 209, 217, 0.55);
}

/* ===== Compare panel ===== */
.compare-panel {
  padding: 20px 22px;
  border-radius: 14px;
  border: 1px solid rgba(151, 117, 250, 0.25);
  background: linear-gradient(135deg, rgba(33, 38, 45, 0.75), rgba(13, 17, 23, 0.75));
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.compare-panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.compare-panel__head strong { color: #b692f6; }

.compare-panel__hint {
  display: block;
  font-size: 11.5px;
  color: rgba(201, 209, 217, 0.55);
  margin-top: 2px;
}

.compare-panel__loading {
  display: flex;
  align-items: center;
  gap: 10px;
  color: rgba(201, 209, 217, 0.65);
}

.compare-panel__legend {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.compare-legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 11.5px;
  color: #c9d1d9;
}

.compare-legend-item__swatch {
  width: 10px;
  height: 10px;
  border-radius: 2px;
  background: var(--legend-color, #58a6ff);
  display: inline-block;
}

.compare-panel__chart {
  width: 100%;
  overflow-x: auto;
}

.compare-panel__metric-tabs {
  display: flex;
  gap: 4px;
  padding-top: 4px;
  border-top: 1px dashed rgba(110, 118, 129, 0.2);
}

.metric-tab {
  font: inherit;
  font-size: 12px;
  padding: 6px 12px;
  border-radius: 6px;
  border: 1px solid transparent;
  background: rgba(33, 38, 45, 0.55);
  color: rgba(201, 209, 217, 0.7);
  cursor: pointer;
  transition: background 0.12s, color 0.12s, border-color 0.12s;
}

.metric-tab:hover {
  background: rgba(88, 166, 255, 0.08);
  color: #79b8ff;
}

.metric-tab--active {
  background: rgba(88, 166, 255, 0.18);
  color: #79b8ff;
  border-color: rgba(88, 166, 255, 0.35);
}

/* ===== Compare checkbox column ===== */
.table__col--check {
  width: 36px;
  text-align: center;
  padding: 4px 0 !important;
}

.th-tooltip {
  display: inline-block;
  color: rgba(201, 209, 217, 0.55);
  font-weight: 700;
}

.compare-checkbox {
  cursor: pointer;
  accent-color: #b692f6;
  width: 16px;
  height: 16px;
}

/* ===== Expanded-row detail panes ===== */
.detail-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr);
  gap: 16px;
  margin-bottom: 18px;
}

@media (max-width: 1100px) {
  .detail-layout { grid-template-columns: 1fr; }
}

.detail-pane {
  padding: 14px 16px;
  border-radius: 12px;
  border: 1px solid rgba(110, 118, 129, 0.18);
  background: linear-gradient(135deg, rgba(33, 38, 45, 0.65), rgba(13, 17, 23, 0.65));
  min-height: 240px;
  display: flex;
  flex-direction: column;
}

.detail-pane__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 8px;
}

.detail-pane__title {
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-weight: 700;
  color: rgba(201, 209, 217, 0.72);
}

.detail-pane__hint {
  font-size: 11px;
  color: rgba(201, 209, 217, 0.45);
}

.detail-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  flex: 1;
  color: rgba(201, 209, 217, 0.55);
  font-size: 12px;
}

.detail-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 1;
  color: rgba(110, 118, 129, 0.7);
  font-size: 13px;
}

.detail-chart,
.detail-funnel {
  flex: 1;
}

.detail-chart svg,
.detail-funnel svg {
  width: 100%;
  height: auto;
  display: block;
}

/* ===== Campaign Builder modal ===== */
.cb-overlay {
  position: fixed;
  inset: 0;
  background: rgba(13, 17, 23, 0.78);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  z-index: 9000;
  overflow-y: auto;
  padding: 40px 20px 60px;
}

.cb-modal {
  width: min(960px, 100%);
  background: linear-gradient(135deg, rgba(33, 38, 45, 0.96), rgba(13, 17, 23, 0.98));
  border: 1px solid rgba(151, 117, 250, 0.32);
  border-radius: 16px;
  box-shadow: 0 30px 80px rgba(0, 0, 0, 0.55);
  color: #c9d1d9;
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 24px 26px 22px;
  font-family: Inter, sans-serif;
}

.cb-modal__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid rgba(110, 118, 129, 0.18);
}

.cb-modal__kicker {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-weight: 700;
  color: #b692f6;
}

.cb-modal__title {
  margin: 4px 0 6px;
  font-size: 22px;
  font-weight: 800;
  color: #f0f6fc;
}

.cb-modal__sub {
  margin: 0;
  font-size: 13px;
  color: rgba(201, 209, 217, 0.7);
  max-width: 640px;
  line-height: 1.45;
}

.cb-modal__close {
  background: rgba(110, 118, 129, 0.12);
  border: 1px solid rgba(110, 118, 129, 0.25);
  color: #c9d1d9;
  font-size: 20px;
  width: 36px;
  height: 36px;
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: background 0.12s, border-color 0.12s;
}

.cb-modal__close:hover {
  background: rgba(255, 107, 107, 0.12);
  border-color: rgba(255, 107, 107, 0.45);
  color: #ff6b6b;
}

.cb-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.cb-section__title {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-weight: 700;
  color: rgba(201, 209, 217, 0.62);
}

.cb-form {
  display: grid;
  grid-template-columns: minmax(260px, 1fr) minmax(260px, 1fr);
  gap: 14px;
}

@media (max-width: 720px) {
  .cb-form { grid-template-columns: 1fr; }
}

.cb-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.cb-field__label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-weight: 700;
  color: rgba(201, 209, 217, 0.62);
}

.cb-input {
  background: rgba(13, 17, 23, 0.65);
  border: 1px solid rgba(110, 118, 129, 0.32);
  border-radius: 8px;
  padding: 10px 12px;
  color: #f0f6fc;
  font-size: 13px;
  font-family: ui-monospace, SFMono-Regular, monospace;
  transition: border-color 0.12s, background 0.12s;
}

.cb-input:focus {
  outline: 0;
  border-color: rgba(151, 117, 250, 0.55);
  background: rgba(13, 17, 23, 0.85);
}

.cb-field__hint {
  font-size: 11px;
  color: rgba(201, 209, 217, 0.5);
}

.cb-field__error {
  font-size: 11.5px;
  color: #ff6b6b;
  font-weight: 600;
}

.cb-templates {
  margin-top: 6px;
}

.cb-templates__title {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-weight: 700;
  color: rgba(201, 209, 217, 0.62);
  margin-bottom: 6px;
}

.cb-templates__list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.cb-tpl-btn {
  font: inherit;
  font-size: 11.5px;
  padding: 5px 11px;
  border-radius: 999px;
  border: 1px solid rgba(151, 117, 250, 0.25);
  background: rgba(151, 117, 250, 0.08);
  color: #b692f6;
  cursor: pointer;
  transition: background 0.12s, border-color 0.12s, color 0.12s;
}

.cb-tpl-btn:hover {
  background: rgba(151, 117, 250, 0.18);
  border-color: rgba(151, 117, 250, 0.55);
  color: #d6c0ff;
}

.cb-urls {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 12px;
}

.cb-url {
  padding: 14px;
  border-radius: 12px;
  border: 1px solid rgba(110, 118, 129, 0.18);
  background: linear-gradient(135deg, rgba(33, 38, 45, 0.55), rgba(13, 17, 23, 0.55));
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.cb-url__head {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 700;
  font-size: 12px;
  color: #79b8ff;
}

.cb-url__value {
  display: block;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 11.5px;
  color: #c9d1d9;
  background: rgba(13, 17, 23, 0.85);
  padding: 8px 10px;
  border-radius: 6px;
  overflow-wrap: break-word;
  word-break: break-all;
  line-height: 1.35;
  border: 1px solid rgba(110, 118, 129, 0.18);
}

.cb-url__actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.cb-mini-btn {
  font: inherit;
  font-size: 11.5px;
  padding: 5px 10px;
  border-radius: 6px;
  border: 1px solid rgba(110, 118, 129, 0.32);
  background: rgba(33, 38, 45, 0.55);
  color: #c9d1d9;
  cursor: pointer;
  transition: background 0.12s, color 0.12s, border-color 0.12s;
}

.cb-mini-btn:hover {
  background: rgba(88, 166, 255, 0.16);
  color: #79b8ff;
  border-color: rgba(88, 166, 255, 0.45);
}

.cb-url__qr {
  display: flex;
  justify-content: center;
}

.cb-snips {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

@media (max-width: 720px) {
  .cb-snips { grid-template-columns: 1fr; }
}

.cb-snip {
  padding: 12px 14px;
  border-radius: 12px;
  border: 1px solid rgba(110, 118, 129, 0.18);
  background: linear-gradient(135deg, rgba(33, 38, 45, 0.55), rgba(13, 17, 23, 0.55));
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.cb-snip__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.cb-snip__name {
  font-size: 12px;
  font-weight: 700;
  color: rgba(201, 209, 217, 0.72);
}

.cb-snip__body {
  font: inherit;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 11.5px;
  line-height: 1.45;
  background: rgba(13, 17, 23, 0.85);
  color: #c9d1d9;
  border: 1px solid rgba(110, 118, 129, 0.22);
  border-radius: 6px;
  padding: 8px 10px;
  resize: vertical;
  min-height: 88px;
}

.cb-snip__hint {
  font-size: 11px;
  color: rgba(201, 209, 217, 0.5);
}

.cb-modal__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding-top: 14px;
  border-top: 1px solid rgba(110, 118, 129, 0.18);
}

.cb-foot__hint {
  font-size: 11.5px;
  color: rgba(201, 209, 217, 0.55);
  max-width: 560px;
  line-height: 1.45;
}

.cb-foot__actions {
  display: flex;
  gap: 8px;
}

.cb-qr-error {
  font-size: 11px;
  color: #ff6b6b;
  padding: 8px;
  background: rgba(255, 107, 107, 0.08);
  border-radius: 6px;
}

.state-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 56px 32px;
  background: #161b22;
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 14px;
  color: #8b949e;
  text-align: center;
}

.state-card--error {
  border-color: rgba(248, 81, 73, 0.4);
}

.state-card__title {
  color: #f0f6fc;
  font-weight: 600;
}

.state-card__hint {
  color: #8b949e;
  font-size: 13px;
}

.footer-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
  font-size: 12px;
  color: #6e7681;
  padding-top: 4px;
}
</style>
