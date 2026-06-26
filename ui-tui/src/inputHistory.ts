/**
 * Input history for the TUI composer.
 * Stores submitted messages and provides up/down navigation.
 */

export class InputHistory {
  private entries: string[] = []
  private index = -1
  private pending = ''

  push(entry: string): void {
    if (!entry.trim()) return
    // Avoid duplicating the last entry
    if (this.entries[this.entries.length - 1] !== entry) {
      this.entries.push(entry)
    }
    this.index = -1
    this.pending = ''
  }

  up(currentInput: string): string {
    if (this.entries.length === 0) return currentInput

    if (this.index === -1) {
      this.pending = currentInput
      this.index = this.entries.length - 1
    } else if (this.index > 0) {
      this.index -= 1
    }

    return this.entries[this.index] ?? currentInput
  }

  down(_currentInput: string): string {
    if (this.index === -1) return _currentInput

    if (this.index < this.entries.length - 1) {
      this.index += 1
      return this.entries[this.index] ?? _currentInput
    }

    this.index = -1
    return this.pending
  }

  reset(): void {
    this.index = -1
    this.pending = ''
  }

  get length(): number {
    return this.entries.length
  }
}
