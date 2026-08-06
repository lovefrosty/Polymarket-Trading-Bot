## What changed

<!-- Describe the bounded change and the user, operator, or research problem it addresses. -->

## Evidence

Evidence class (check all that apply):

- [ ] Fixture or unit-test data
- [ ] Synthetic data
- [ ] Historical market data
- [ ] Live public market data
- [ ] Paper execution
- [ ] Exchange-confirmed live fills

Commands and results:

```text
python -m pytest -q
```

## Assumptions and limitations

<!-- Include fee, latency, fill, queue-position, capital, and jurisdiction assumptions when relevant. -->

## Safety checklist

- [ ] No credentials, private keys, seed phrases, or funded `.env` files are included.
- [ ] Simulated results are not described as live performance.
- [ ] New execution behavior defaults to disabled or fail-closed.
- [ ] Risk limits and cancel/kill-switch behavior are preserved where applicable.
- [ ] Documentation was updated if the operating contract changed.
- [ ] I reviewed the diff and included only intended files.
