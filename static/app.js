class CurrencyConverter {
  constructor() {
    this.amount = document.querySelector('#amount')
    this.source = document.querySelector('#source')
    this.target = document.querySelector('#target')
    this.result = document.querySelector('#result')
    this.error = document.querySelector('#error')
    this.hint = document.querySelector('#hint')
    this.currencies = new Map()
    this.timer = null
  }

  async start() {
    const response = await fetch('/api/currencies')
    const currencies = await response.json()
    this.currencies = new Map(currencies.map((currency) => [currency.alpha, currency]))
    this.source.addEventListener('change', () => this.update())
    this.target.addEventListener('change', () => this.update())
    this.amount.addEventListener('input', () => this.update())
    document.querySelector('#swap').addEventListener('click', () => this.swap())
    this.update()
  }

  swap() {
    const sourceCode = this.source.value
    this.source.value = this.target.value
    this.target.value = sourceCode
    this.update()
  }

  updateCurrencyDetails() {
    for (const [side, select] of [['source', this.source], ['target', this.target]]) {
      const currency = this.currencies.get(select.value)
      document.querySelector(`#${side}-code-label`).textContent = currency.alpha
      document.querySelector(`#${side}-name`).textContent = currency.name
      document.querySelector(`#${side}-precision`).textContent = `${currency.minor_units} décimale${currency.minor_units === 1 ? '' : 's'}`
    }
  }

  async update() {
    clearTimeout(this.timer)
    this.updateCurrencyDetails()
    this.error.hidden = true
    this.hint.hidden = true
    document.querySelector('#stale-banner').hidden = true
    const rawAmount = this.amount.value.trim()
    if (!rawAmount) {
      this.showEmpty('Saisissez un montant pour afficher la conversion.')
      return
    }
    if (/^\d+[.,]$/.test(rawAmount)) {
      this.showEmpty('Terminez la saisie du montant.')
      return
    }
    this.timer = setTimeout(() => this.convert(rawAmount), 120)
  }

  showEmpty(message) {
    this.result.textContent = '—'
    this.hint.textContent = message
    this.hint.hidden = false
    document.querySelector('#rate').textContent = 'En attente du montant'
  }

  async convert(amount) {
    try {
      const response = await fetch('/api/convert', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ amount, source: this.source.value, target: this.target.value }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(payload.error || 'Conversion impossible.')
      this.result.textContent = this.formatMoney(payload.to)
      document.querySelector('#rate').textContent = `1 ${payload.from.currency} = ${payload.rate_used.replace('.', ',')} ${payload.to.currency}`
      document.querySelector('#source-name-meta').textContent = payload.source
      document.querySelector('#updated-at').textContent = this.formatDate(payload.as_of)
      this.showStale(payload)
    } catch (error) {
      this.result.textContent = '—'
      this.error.textContent = error instanceof TypeError ? 'Service indisponible. Vérifiez que le serveur Python est lancé.' : error.message
      this.error.hidden = false
      document.querySelector('#rate').textContent = 'Taux indisponible'
    }
  }

  formatMoney(money) {
    const currency = this.currencies.get(money.currency)
    const scale = 10n ** BigInt(currency.minor_units)
    const minor = BigInt(money.amount_minor)
    const whole = minor / scale
    const fraction = (minor % scale).toString().padStart(currency.minor_units, '0')
    const formatter = new Intl.NumberFormat('fr-FR', {
      style: 'currency', currency: money.currency, minimumFractionDigits: 0, maximumFractionDigits: 0,
    })
    const parts = formatter.formatToParts(whole)
    if (currency.minor_units > 0) {
      const decimal = new Intl.NumberFormat('fr-FR').formatToParts(1.1).find((part) => part.type === 'decimal').value
      const digits = [...fraction].map((digit) => new Intl.NumberFormat('fr-FR', { useGrouping: false }).format(BigInt(digit))).join('')
      const lastInteger = parts.reduce((last, part, index) => part.type === 'integer' ? index : last, -1)
      parts.splice(lastInteger + 1, 0, { type: 'decimal', value: decimal }, { type: 'fraction', value: digits })
    }
    return parts.map((part) => part.value).join('')
  }

  formatDate(value) {
    const date = new Date(value)
    return `${new Intl.DateTimeFormat('fr-FR', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' }).format(date)} UTC`
  }

  showStale(payload) {
    const banner = document.querySelector('#stale-banner')
    if (!payload.is_stale) return
    banner.textContent = `Taux périmé, mis à jour le ${this.formatDate(payload.as_of)}. Vérifiez une source à jour avant toute décision.`
    banner.hidden = false
  }
}

new CurrencyConverter().start().catch(() => {
  const message = document.querySelector('#error')
  message.textContent = 'Impossible de charger le catalogue de devises. Vérifiez que le serveur Python est lancé.'
  message.hidden = false
})