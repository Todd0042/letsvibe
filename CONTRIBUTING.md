# Contributing to LetsVibe

Thanks for your interest! LetsVibe is a one-person project with a live site, so contributions work like this:

## Welcome

* **Bug reports and ideas**: open an [issue](https://github.com/Todd0042/letsvibe/issues). For a bug,
  include what you did, what you expected and what happened; for a grading problem, the repository link and
  the statement or score you believe is wrong.
* **Grading disputes for a project on the live site**: use **Report an error in this review** on your
  project page first; it is checked against your code automatically.
* **Security problems**: please don't open an issue; see [SECURITY.md](SECURITY.md).

## Code contributions

Pull requests with code are **not being accepted for now**, so the grading rubric, the curve calibration and
the privacy guarantees stay consistent and fully explainable. Ideas in issues are very welcome and may well
end up in the code.

## Running it locally

See **Run It Yourself** and **Repository Structure & Tests** in the [README](README.md). Please run the tests
before reporting a code problem:

```bash
.venv/bin/python -m pytest -q --ignore=tests/ui
```

## License

LetsVibe is licensed under the [PolyForm Shield License 1.0.0](LICENSE). The LetsVibe name and logo are not
licensed.
