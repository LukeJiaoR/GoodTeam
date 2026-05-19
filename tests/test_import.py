def test_import_package():
    import goodteam_os

    assert goodteam_os.__version__ == "0.1.0"
