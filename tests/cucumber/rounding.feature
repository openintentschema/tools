@billing
Feature: Invoices round to five Rappen
  Background:
    Given the currency is CHF

  @AC-1 @smoke
  Scenario: Round down
    Given an invoice total of CHF 10.02
    When the invoice is issued
    Then the amount due is CHF 10.00

  @AC-2
  Scenario Outline: Reject negative amounts
    When an amount of <amount> is entered
    Then the invoice is rejected

    Examples:
      | amount |
      | -1     |
      | -0.05  |

  @AC-3
  Scenario: Round up
    Given an invoice total of CHF 10.03
    When the invoice is issued
    Then the amount due is CHF 10.05

  @AC-4
  Scenario: Credit notes
    Given a credit note of CHF 5.02
    When the credit note is issued
    Then the amount credited is CHF 5.00

  Scenario: Exploratory, no key
    Given an invoice total of CHF 0.00
    When the invoice is issued
    Then the amount due is CHF 0.00
