"""Utilitaires de théorie musicale"""

def key_mode_to_french(key: int, mode: int) -> str:
    """
    Convertit key (0-11) + mode (0-1) en notation française
    
    Args:
        key: Pitch class (0=C, 1=C#, 2=D, etc.)
        mode: 0=mineur, 1=majeur
        
    Returns:
        Tonalité en français (ex: "Ré mineur", "Do majeur")
    """
    # Correspondance Pitch Class -> Note française
    notes_fr = {
        0: "Do",
        1: "Do#/Réb",
        2: "Ré",
        3: "Ré#/Mib",
        4: "Mi",
        5: "Fa",
        6: "Fa#/Solb",
        7: "Sol",
        8: "Sol#/Lab",
        9: "La",
        10: "La#/Sib",
        11: "Si"
    }
    
    # Mode
    mode_fr = "majeur" if mode == 1 else "mineur"
    
    # Tonalité complète
    note = notes_fr.get(key, "Inconnu")
    return f"{note} {mode_fr}"


def key_mode_to_french_from_string(key_str: str, mode_str: str) -> str:
    """
    Convertit key (string comme "F", "C#") + mode (string comme "major", "minor") 
    en notation française
    
    Args:
        key_str: Note en notation anglaise (ex: "F", "C#", "Bb") ou entier
        mode_str: Mode en anglais ("major" ou "minor") ou entier
        
    Returns:
        Tonalité en français (ex: "Fa majeur", "Ré mineur")
    """
    # NOUVEAU: Gérer le cas où on reçoit des entiers
    if isinstance(key_str, (int, float)) and isinstance(mode_str, (int, float)):
        return key_mode_to_french(int(key_str), int(mode_str))
    
    # Mapping des notes anglaises vers françaises
    notes_mapping = {
        "C": "Do",
        "C#": "Do#", "Db": "Réb",
        "D": "Ré",
        "D#": "Ré#", "Eb": "Mib",
        "E": "Mi",
        "F": "Fa",
        "F#": "Fa#", "Gb": "Solb",
        "G": "Sol",
        "G#": "Sol#", "Ab": "Lab",
        "A": "La",
        "A#": "La#", "Bb": "Sib",
        "B": "Si"
    }
    
    # Nettoyer la key - CORRECTION: vérifier le type avant strip()
    key_clean = str(key_str).strip() if isinstance(key_str, str) else str(key_str)
    
    # Trouver la note française
    note_fr = notes_mapping.get(key_clean, key_clean)
    
    # Convertir le mode - CORRECTION: vérifier le type avant strip()
    mode_clean = str(mode_str).strip().lower() if isinstance(mode_str, str) else str(mode_str)
    mode_fr = "majeur" if mode_clean in ["major", "1"] else "mineur"
    
    return f"{note_fr} {mode_fr}"


def key_mode_to_english(key: int, mode: int) -> str:
    """Version anglaise pour référence"""
    notes_en = {
        0: "C", 1: "C#/Db", 2: "D", 3: "D#/Eb",
        4: "E", 5: "F", 6: "F#/Gb", 7: "G",
        8: "G#/Ab", 9: "A", 10: "A#/Bb", 11: "B"
    }
    
    mode_en = "major" if mode == 1 else "minor"
    note = notes_en.get(key, "Unknown")
    return f"{note} {mode_en}"