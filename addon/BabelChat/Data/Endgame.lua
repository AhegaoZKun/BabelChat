local ADDON_NAME, addonTable = ...

-- Endgame / Midnight-era terminology (The War Within 11.x → Midnight 12.x).
-- Covers terms the legacy dictionary predates: delves, M+ shorthand, gear
-- tracks, upgrade crests, Warbands, catalyst/spark, raider.io, etc.
--
-- enUS is the authoritative anchor. Translations for the major locales
-- (esES/esMX/deDE/frFR/ptBR/itIT/ruRU) use Blizzard's official in-game terms
-- where they exist; abbreviations (M+, RIO, KSM, GINV) stay as the universal
-- token expanded in-language. NOTE: koKR / zhCN / zhTW / plPL / svSE / noNO
-- cells for the noun terms are best-effort and SHOULD BE VERIFIED by native
-- players before the next CurseForge/Wago release.

addonTable.EndgameDict = {
    ["delve"] = {
        enUS = "Delve (solo/small-group instance)",
        esES = "Caverna", esMX = "Caverna",
        deDE = "Tiefe", frFR = "Gouffre", itIT = "Antro",
        ptBR = "Vão", ruRU = "Вылазка",
        koKR = "구렁(델브)", zhCN = "地下堡", zhTW = "地下堡",
        plPL = "Otchłań (Delve)", svSE = "Delve", noNO = "Delve",
    },
    ["delves"] = {
        enUS = "Delves",
        esES = "Cavernas", esMX = "Cavernas",
        deDE = "Tiefen", frFR = "Gouffres", itIT = "Antri",
        ptBR = "Vãos", ruRU = "Вылазки",
        koKR = "구렁(델브)", zhCN = "地下堡", zhTW = "地下堡",
        plPL = "Otchłanie (Delves)", svSE = "Delves", noNO = "Delves",
    },
    ["bountiful"] = {
        enUS = "Bountiful Delve (rewards chest, needs key)",
        esES = "Caverna abundante", esMX = "Caverna abundante",
        deDE = "Ergiebige Tiefe", frFR = "Gouffre fructueux", itIT = "Antro fruttuoso",
        ptBR = "Vão farto", ruRU = "Щедрая вылазка",
        koKR = "풍요로운 구렁", zhCN = "丰饶地下堡", zhTW = "豐饒地下堡",
        plPL = "Obfita otchłań", svSE = "Bountiful delve", noNO = "Bountiful delve",
    },
    ["brann"] = {
        enUS = "Brann Bronzebeard (Delve companion)",
        esES = "Brann (compañero de cavernas)", esMX = "Brann (compañero de cavernas)",
        deDE = "Brann (Tiefen-Begleiter)", frFR = "Brann (compagnon de gouffre)", itIT = "Brann (compagno antri)",
        ptBR = "Brann (companheiro de vãos)", ruRU = "Бранн (спутник в вылазках)",
        koKR = "브란 (구렁 동료)", zhCN = "布兰(地下堡同伴)", zhTW = "布蘭(地下堡同伴)",
        plPL = "Brann (towarzysz otchłani)", svSE = "Brann (delve-följeslagare)", noNO = "Brann (delve-følgesvenn)",
    },
    ["m+"] = {
        enUS = "Mythic+ (timed keystone dungeon)",
        esES = "Mítica+", esMX = "Mítica+",
        deDE = "Mythisch+", frFR = "Mythique+", itIT = "Mitica+",
        ptBR = "Mítica+", ruRU = "Мифик+",
        koKR = "쐐기돌(신화+)", zhCN = "大秘境(M+)", zhTW = "傳奇+鑰石",
        plPL = "Mityczne+ (M+)", svSE = "Mythic+", noNO = "Mythic+",
    },
    ["keystone"] = {
        enUS = "Mythic+ Keystone",
        esES = "Piedra angular", esMX = "Piedra angular",
        deDE = "Schlüsselstein", frFR = "Pierre angulaire", itIT = "Chiave di volta",
        ptBR = "Pedra angular", ruRU = "Камень ключа",
        koKR = "쐐기돌", zhCN = "钥石", zhTW = "鑰石",
        plPL = "Kamień klucza", svSE = "Nyckelsten", noNO = "Nøkkelstein",
    },
    ["affix"] = {
        enUS = "M+ Affix (weekly modifier)",
        esES = "Sufijo (modificador semanal)", esMX = "Sufijo (modificador semanal)",
        deDE = "Affix (Wochenmodifikator)", frFR = "Affixe (modificateur hebdo)", itIT = "Affisso (modificatore settimanale)",
        ptBR = "Afixo (modificador semanal)", ruRU = "Аффикс (модификатор недели)",
        koKR = "어픽스(주간 변수)", zhCN = "词缀", zhTW = "詞綴",
        plPL = "Afiks (modyfikator tygodnia)", svSE = "Affix", noNO = "Affiks",
    },
    ["fortified"] = {
        enUS = "Fortified (affix: tougher trash)",
        esES = "Fortificado", esMX = "Fortificado",
        deDE = "Verstärkt", frFR = "Fortifié", itIT = "Fortificato",
        ptBR = "Fortificado", ruRU = "Укреплённый",
        koKR = "보강", zhCN = "强韧", zhTW = "強韌",
        plPL = "Wzmocnione", svSE = "Fortified", noNO = "Fortified",
    },
    ["tyrannical"] = {
        enUS = "Tyrannical (affix: tougher bosses)",
        esES = "Tiránico", esMX = "Tiránico",
        deDE = "Tyrannisch", frFR = "Tyrannique", itIT = "Tirannico",
        ptBR = "Tirânico", ruRU = "Тиранический",
        koKR = "포악", zhCN = "暴君", zhTW = "暴虐",
        plPL = "Tyraniczne", svSE = "Tyrannical", noNO = "Tyrannical",
    },
    ["rio"] = {
        enUS = "Raider.IO score (M+ rating)",
        esES = "Puntuación Raider.IO", esMX = "Puntuación Raider.IO",
        deDE = "Raider.IO-Wertung", frFR = "Score Raider.IO", itIT = "Punteggio Raider.IO",
        ptBR = "Pontuação Raider.IO", ruRU = "Рейтинг Raider.IO",
        koKR = "Raider.IO 점수", zhCN = "RIO 评分", zhTW = "RIO 評分",
        plPL = "Wynik Raider.IO", svSE = "Raider.IO-poäng", noNO = "Raider.IO-poeng",
    },
    ["ksm"] = {
        enUS = "Keystone Master (all M+ at +10 in time)",
        esES = "Maestro de piedras angulares", esMX = "Maestro de piedras angulares",
        deDE = "Schlüsselsteinmeister", frFR = "Maître des pierres angulaires", itIT = "Maestro delle chiavi di volta",
        ptBR = "Mestre das pedras angulares", ruRU = "Мастер ключей (KSM)",
        koKR = "쐐기돌 마스터", zhCN = "钥石大师", zhTW = "鑰石大師",
        plPL = "Mistrz kamieni klucza", svSE = "Keystone Master", noNO = "Keystone Master",
    },
    ["warband"] = {
        enUS = "Warband (account-wide roster/bank)",
        esES = "Tropa de guerra", esMX = "Tropa de guerra",
        deDE = "Kriegsmeute", frFR = "Bataillon", itIT = "Manipolo",
        ptBR = "Tropa de guerra", ruRU = "Военный отряд (варбэнд)",
        koKR = "전투부대", zhCN = "战团", zhTW = "戰隊",
        plPL = "Drużyna wojenna", svSE = "Warband", noNO = "Warband",
    },
    ["catalyst"] = {
        enUS = "Catalyst (convert gear to tier set)",
        esES = "Catalizador", esMX = "Catalizador",
        deDE = "Katalysator", frFR = "Catalyseur", itIT = "Catalizzatore",
        ptBR = "Catalisador", ruRU = "Катализатор",
        koKR = "촉매", zhCN = "催化器", zhTW = "催化器",
        plPL = "Katalizator", svSE = "Katalysator", noNO = "Katalysator",
    },
    ["spark"] = {
        enUS = "Spark (crafting reagent, weekly)",
        esES = "Chispa", esMX = "Chispa",
        deDE = "Funke", frFR = "Étincelle", itIT = "Scintilla",
        ptBR = "Faísca", ruRU = "Искра",
        koKR = "불꽃", zhCN = "火花", zhTW = "火花",
        plPL = "Iskra", svSE = "Gnista", noNO = "Gnist",
    },
    ["renown"] = {
        enUS = "Renown (faction reputation track)",
        esES = "Renombre", esMX = "Renombre",
        deDE = "Ruf (Renown)", frFR = "Renommée", itIT = "Fama",
        ptBR = "Renome", ruRU = "Известность",
        koKR = "명성", zhCN = "声望", zhTW = "聲望",
        plPL = "Renoma", svSE = "Renown", noNO = "Renown",
    },
    ["champion"] = {
        enUS = "Champion (gear track, below Hero)",
        esES = "Campeón (vía de equipo)", esMX = "Campeón (vía de equipo)",
        deDE = "Champion (Ausrüstungsstufe)", frFR = "Champion (palier d'équipement)", itIT = "Campione (traccia equip.)",
        ptBR = "Campeão (trilha de equip.)", ruRU = "Чемпион (трек экипировки)",
        koKR = "챔피언(장비 등급)", zhCN = "勇士(装备等级)", zhTW = "勇士(裝備等級)",
        plPL = "Mistrz (ścieżka ekwipunku)", svSE = "Champion (gear track)", noNO = "Champion (gear track)",
    },
    ["myth"] = {
        enUS = "Myth (top gear track)",
        esES = "Mito (vía de equipo)", esMX = "Mito (vía de equipo)",
        deDE = "Mythos (Ausrüstungsstufe)", frFR = "Mythe (palier d'équipement)", itIT = "Mito (traccia equip.)",
        ptBR = "Mito (trilha de equip.)", ruRU = "Миф (трек экипировки)",
        koKR = "신화(장비 등급)", zhCN = "传说(装备等级)", zhTW = "傳說(裝備等級)",
        plPL = "Mit (ścieżka ekwipunku)", svSE = "Myth (gear track)", noNO = "Myth (gear track)",
    },
    ["gilded"] = {
        enUS = "Gilded Crest (top upgrade currency)",
        esES = "Blasón dorado", esMX = "Blasón dorado",
        deDE = "Vergoldetes Wappen", frFR = "Écusson doré", itIT = "Stemma dorato",
        ptBR = "Brasão dourado", ruRU = "Золочёная эмблема",
        koKR = "도금 문장", zhCN = "镀金纹章", zhTW = "鍍金紋章",
        plPL = "Złocony herb", svSE = "Gilded Crest", noNO = "Gilded Crest",
    },
    ["runed"] = {
        enUS = "Runed Crest (upgrade currency)",
        esES = "Blasón rúnico", esMX = "Blasón rúnico",
        deDE = "Runenverziertes Wappen", frFR = "Écusson runique", itIT = "Stemma runico",
        ptBR = "Brasão rúnico", ruRU = "Руническая эмблема",
        koKR = "룬 문장", zhCN = "符文纹章", zhTW = "符文紋章",
        plPL = "Runiczny herb", svSE = "Runed Crest", noNO = "Runed Crest",
    },
    ["ginv"] = {
        enUS = "Guild invite request (please invite me to the guild)",
        esES = "Invítame a la hermandad", esMX = "Invítame a la hermandad",
        deDE = "Gildeneinladung erbeten", frFR = "Invitation de guilde demandée", itIT = "Invito in gilda richiesto",
        ptBR = "Convite de guilda, por favor", ruRU = "Пригласите в гильдию",
        koKR = "길드 초대 요청", zhCN = "求公会邀请", zhTW = "求公會邀請",
        plPL = "Proszę o zaproszenie do gildii", svSE = "Gildinbjudan tack", noNO = "Gildeinvitasjon takk",
    },
    ["manaforge"] = {
        enUS = "Manaforge Omega (current raid)",
        esES = "Manaforja Omega", esMX = "Manaforja Omega",
        deDE = "Manaschmiede Omega", frFR = "Forge de mana Oméga", itIT = "Manaforgia Omega",
        ptBR = "Forja de Mana Ômega", ruRU = "Манакузня «Омега»",
        koKR = "마나괴철로 오메가", zhCN = "法力熔炉:欧米加", zhTW = "法力熔爐:歐米加",
        plPL = "Manakuźnia Omega", svSE = "Manaforge Omega", noNO = "Manaforge Omega",
    },
    ["undermine"] = {
        -- Proper noun (goblin city, patch 11.1) — kept untranslated in
        -- Latin-script locales; CJK/RU use the official localized names.
        enUS = "Undermine (goblin city zone, 11.1)",
        esES = "Undermine", esMX = "Undermine",
        deDE = "Undermine", frFR = "Undermine", itIT = "Undermine",
        ptBR = "Undermine", ruRU = "Undermine (Подкоп)",
        koKR = "언더마인", zhCN = "加基森", zhTW = "加基森",
        plPL = "Undermine", svSE = "Undermine", noNO = "Undermine",
    },
}
