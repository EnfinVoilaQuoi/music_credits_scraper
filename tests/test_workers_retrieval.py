def test_discographie_chargee_lit_l_artiste_courant():
    """`app.tracks` n'est rempli que par le worker : la discographie chargée est
    sur `app.current_artist.tracks` (bouton « Écarts Deezer » grisé à tort)."""
    from types import SimpleNamespace

    from src.gui.workers.retrieval import discographie_chargee

    assert discographie_chargee(SimpleNamespace(current_artist=None, tracks=[])) == []
    app = SimpleNamespace(current_artist=SimpleNamespace(tracks=["a", "b"]), tracks=[])
    assert discographie_chargee(app) == ["a", "b"]
