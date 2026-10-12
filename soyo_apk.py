import math
import copy
import re

# =====================================================================
# 1. CONSTANTES Y REGLA OFICIAL DE DEMASÍA
# =====================================================================
DEMASIA_UMBRAL_BAJO = 500    # por debajo: demasía fija (50 por defecto)
DEMASIA_UMBRAL_ALTO = 1000   # por encima: 10 % con tope
DEMASIA_PORCENTAJE = 10      # %
DEMASIA_TOPE = 200           # piezas máximas del porcentaje cuando se pasa de UMBRAL_ALTO

def demasia_por_cantidad(cantidad, base=50):
    """< 500 u.: valor fijo (base) | 500-1000 u.: 10 % | > 1000 u.: 10 % con tope de 200 piezas."""
    if cantidad < DEMASIA_UMBRAL_BAJO:
        return base
    d = (cantidad * DEMASIA_PORCENTAJE + 99) // 100      # 10 % redondeado hacia arriba
    if cantidad > DEMASIA_UMBRAL_ALTO:
        d = min(d, DEMASIA_TOPE)
    return d

def nivel_saneamiento(val):
    """0 = verde (<=15) | 1 = azul (<=30) | 2 = ALERTA (<=50) | 3 = ALERTA CRÍTICA (>50)"""
    if val <= 15:
        return 0
    if val <= 30:
        return 1
    if val <= 50:
        return 2
    return 3


# =====================================================================
# 2. MOTOR DE CORTE LINEAL CONTINUO
# =====================================================================
EXTRAS_LINEAL = {"CAJONES": 15, "L'S": 10, "GUALDERAS": 10}
EXTRA_PATRON_LINEAL = 30
LARGO_TIRA_LINEAL = 3000
MODOS_LINEAL = list(EXTRAS_LINEAL.keys())

def calcular_lineal(lineas, largo_tira=LARGO_TIRA_LINEAL, extra_patron=EXTRA_PATRON_LINEAL, extras_modo=None, dem_base=50):
    if extras_modo is None:
        extras_modo = EXTRAS_LINEAL

    resultados, total_mm = [], 0
    for ln in lineas:
        extra = extras_modo.get(ln["modo"], 0)
        pieza = ln["medida"] + extra
        cant = ln["cant"]
        dem_manual = ln.get("demasia") is not None
        demasia = ln["demasia"] if dem_manual else demasia_por_cantidad(cant, dem_base)
        total_piezas = cant + demasia
        larga = pieza > largo_tira
        
        r = {
            "medida": ln["medida"], "cant": cant, "modo": ln["modo"], "extra": extra, "pieza": pieza,
            "demasia": demasia, "dem_manual": dem_manual, "total_piezas": total_piezas,
            "mult": None, "mult_manual": False, "mult_limitada": False, "n_max": 0,
            "patron_len": 0, "full": 0, "rem": 0, "resto_len": 0, "extra_piezas": 0, "metros_mm": 0, "error": None,
            "larga": larga, "tiras_pieza": math.ceil(pieza / largo_tira) if larga else 1
        }
        n_max = 1 if larga else max(1, int((largo_tira - extra_patron) // pieza))
        mult_manual = ln.get("mult") is not None and ln["mult"] >= 1
        mult = ln["mult"] if mult_manual else (1 if ln["modo"] == "CAJONES" else n_max)
        if mult > n_max:
            mult, r["mult_limitada"] = n_max, True
            
        full = math.ceil(total_piezas / mult)
        patron_len = mult * pieza + (extra_patron if mult > 1 else 0)
        r.update({
            "mult": mult, "mult_manual": mult_manual, "n_max": n_max, "patron_len": patron_len,
            "full": full, "rem": 0, "resto_len": 0, "extra_piezas": full * mult - total_piezas,
            "metros_mm": full * patron_len
        })
        total_mm += r["metros_mm"]
        resultados.append(r)
        
    tiras = math.ceil(total_mm / largo_tira - 1e-9) if total_mm > 0 else 0
    return {"lineas": resultados, "total_mm": total_mm, "tiras": tiras}


# =====================================================================
# 3. CONVERSIÓN A TABLEROS FÍSICOS
# =====================================================================
def calcular_tableros(tiras, cortes):
    try:
        tiras, cortes = int(tiras), int(cortes)
    except (TypeError, ValueError):
        return None
    if cortes < 1 or tiras < 0:
        return None
    tableros = math.ceil(tiras / cortes - 1e-9)
    return {
        "cortes": cortes, "tiras": tiras, "exacto": tiras / cortes, 
        "tableros": tableros, "sobran": tableros * cortes - tiras
    }

def texto_tableros(info):
    sob = f"sobran {info['sobran']} tiras sin usar" if info["sobran"] else "sin tiras sobrantes"
    return f"TABLEROS: {info['tiras']} tiras ÷ {info['cortes']} = {info['exacto']:.2f} → {info['tableros']} u.   ({sob})"


# =====================================================================
# 4. MOTOR COMPLETO DE OPTIMIZACIÓN DE TIRAS (Buscador y Patrones)
# =====================================================================
def ejecutar_motor_optimizacion_completo(largo_tira_objetivo, pedidos_data, params, es_mitad=False):
    try:
        kerf = int(float(params.get("kerf", 10)))
        saneamiento = int(float(params.get("saneamiento", 0)))
        max_medidas = int(float(params.get("pasos", 2)))
        if es_mitad:
            max_medidas = int(float(params.get("medidas_mitad", 2)))
        txt_mp = str(params.get("max_patrones", 0)).strip()
        max_patrones = int(float(txt_mp)) if txt_mp else 0
        holgura = max(0, int(float(str(params.get("holgura", 50)).strip() or 0)))
        min_tiras = max(0, int(float(str(params.get("min_tiras", 0)).strip() or 0)))
        demasia_global = int(float(params.get("demasia_global", 50)))
    except ValueError:
        return {"error": "Parámetros numéricos inválidos en la configuración de tiras."}

    if max_medidas < 1:
        return {"error": "El número máximo de medidas por patrón debe ser al menos 1."}
    max_patrones = max_patrones if max_patrones > 0 else None

    agrupados = {}
    for p in pedidos_data:
        key = (p['medida'], p['modo'])
        if key not in agrupados:
            agrupados[key] = {"cant": 0, "demasia": None}
        agrupados[key]["cant"] += p['cant']
        if p.get('dem_manual') and agrupados[key]["demasia"] is None:
            agrupados[key]["demasia"] = p.get('demasia_manual', 0)

    for datos in agrupados.values():
        if datos["demasia"] is None:
            datos["demasia"] = demasia_por_cantidad(datos["cant"], demasia_global)

    pedidos_proc = []
    piezas_disponibles = {}
    holgura_tramo = {}

    for (medida_base, modo), datos in agrupados.items():
        cant_total = datos["cant"]
        dem = datos["demasia"]
        tot = cant_total + dem

        es_doble = (modo == "Al Doble (2x)")
        es_a_medida = (modo == "A Medida")

        if es_a_medida:
            m_tramo = int(medida_base)
            tramos = tot
        elif es_doble:
            m_tramo = int((medida_base * 2) + kerf)
            tramos = math.ceil(tot / 2)
        else:
            m_tramo = int(medida_base)
            tramos = tot

        pedidos_proc.append({
            "medida_base": medida_base, "Modo": modo, "es_doble": es_doble, "es_a_medida": es_a_medida,
            "m_tramo": m_tramo, "cant_pedida": cant_total, "demasia_base": dem, "demasia_actual": dem,
            "optimizado": False, "tot_piezas": tot, "tramos": tramos
        })
        piezas_disponibles[m_tramo] = piezas_disponibles.get(m_tramo, 0) + tramos
        holgura_tramo[m_tramo] = holgura_tramo.get(m_tramo, 0) + math.ceil(holgura / (2 if es_doble else 1))

    efectivo_largo_tira = largo_tira_objetivo - (saneamiento * 2)

    for m_tramo in piezas_disponibles:
        if m_tramo <= 0:
            return {"error": f"Hay una medida no válida ({m_tramo} mm)."}
        if m_tramo + kerf > efectivo_largo_tira:
            return {"error": f"El tramo de {m_tramo} mm (+{kerf} mm de sierra) no cabe en una tira de {largo_tira_objetivo} mm."}

    def buscar_patrones(tipos, todos_obligatorios=False):
        encontrados = []
        def rec(indice, actual_combinacion, espacio_restante):
            if indice == len(tipos):
                if actual_combinacion:
                    uso_total = sum(p * c for p, c in actual_combinacion.items()) + (sum(actual_combinacion.values()) * kerf)
                    if uso_total <= efectivo_largo_tira:
                        encontrados.append((efectivo_largo_tira - uso_total, actual_combinacion.copy()))
                return
            pza = tipos[indice]
            max_cabe = math.floor(espacio_restante / (pza + kerf))
            for c in range(max_cabe, -1, -1):
                if todos_obligatorios and c == 0:
                    continue
                nueva_comb = actual_combinacion.copy()
                if c > 0:
                    nueva_comb[pza] = c
                if len(nueva_comb) <= max_medidas:
                    rec(indice + 1, nueva_comb, espacio_restante - (c * pza + c * kerf))
        rec(0, {}, efectivo_largo_tira)
        encontrados.sort(key=lambda x: x[0])
        return encontrados

    tipos_piezas = [t for t, n in piezas_disponibles.items() if n > 0]
    if not tipos_piezas:
        return {"error": "No hay piezas que fabricar."}

    necesidad = [piezas_disponibles[t] for t in tipos_piezas]
    n_tipos = len(tipos_piezas)
    holgura_k = [holgura_tramo.get(t, 0) for t in tipos_piezas]

    lb_tiras = math.ceil(sum(necesidad[k] * (tipos_piezas[k] + kerf) for k in range(n_tipos)) / max(1, efectivo_largo_tira))
    min_efectivo = min(min_tiras, max(1, lb_tiras // 2)) if min_tiras > 0 else 0

    if max_patrones is not None:
        minimo_posible = math.ceil(n_tipos / max_medidas)
        if max_patrones < minimo_posible:
            return {"error": f"Hacen falta al menos {minimo_posible} patrones, pero el máximo es {max_patrones}."}

    todos = buscar_patrones(tipos_piezas)
    LIMITE_CANDIDATOS = 80
    def clave_patron(comb):
        return tuple(sorted(comb.items()))

    if len(todos) <= LIMITE_CANDIDATOS:
        lista_cand = [c for _, c in todos]
    else:
        def llena(comb, desperd):
            permitidas = tipos_piezas if len(comb) < max_medidas else list(comb)
            return all(t + kerf > desperd for t in permitidas)
        llenos = [(w, c) for w, c in todos if llena(c, w)] or todos
        elegidos = {}
        for w, c in llenos[:50]:
            elegidos[clave_patron(c)] = c
        for t in tipos_piezas:
            con_t = [c for w, c in llenos if t in c]
            for c in con_t[:6]:
                elegidos[clave_patron(c)] = c
        lista_cand = list(elegidos.values())

    indice_patron = {clave_patron(c): k for k, c in enumerate(lista_cand)}
    n_base = len(lista_cand)
    parejas_extra = set()
    cache_comp = {}
    for _, cand in todos[:500]:
        restantes = tuple(t for t in tipos_piezas if t not in cand)
        if not restantes or len(restantes) > max_medidas:
            continue
        if restantes not in cache_comp:
            res_comp = buscar_patrones(list(restantes), todos_obligatorios=True)
            cache_comp[restantes] = res_comp[0][1] if res_comp else None
        comp = cache_comp[restantes]
        if not comp:
            continue
        ids = []
        for c in (cand, comp):
            k = clave_patron(c)
            if k not in indice_patron:
                indice_patron[k] = len(lista_cand)
                lista_cand.append(c)
            ids.append(indice_patron[k])
        parejas_extra.add((ids[0], ids[1]))
        parejas_extra.add((ids[1], ids[0]))

    vectores = [[c.get(t, 0) for t in tipos_piezas] for c in lista_cand]
    parejas = {(i, j) for i in range(n_base) for j in range(n_base) if i != j} | parejas_extra
    pieza_media = sum(tipos_piezas) / n_tipos

    def exceso_ud(v1, n1, v2, n2):
        e = 0
        for i in range(n_tipos):
            prod = n1 * v1[i] + (n2 * v2[i] if v2 else 0)
            if prod > necesidad[i]:
                e += prod - necesidad[i]
        return e

    def resolver(m_eff):
        def con_min(n):
            n = max(n, m_eff) if n > 0 else 0
            if es_mitad and n % 2:
                n += 1
            return n
        mejor = None
        def probar(i, j, n1, n2, v1, v2):
            nonlocal mejor
            total = n1 + n2
            exc = exceso_ud(v1, n1, v2, n2)
            coste = total * largo_tira_objetivo + exc * pieza_media
            if mejor is None or (coste, total, exc) < mejor[:3]:
                mejor = (coste, total, exc, i, j, n1, n2)

        for i, v in enumerate(vectores):
            if any(necesidad[k] > 0 and v[k] == 0 for k in range(n_tipos)):
                continue
            n = con_min(max(math.ceil(necesidad[k] / v[k]) for k in range(n_tipos)))
            probar(i, None, n, 0, v, None)

        for i, j in parejas:
            v1, v2 = vectores[i], vectores[j]
            if any(v1[k] == 0 and v2[k] == 0 for k in range(n_tipos)):
                continue
            puntos = {m_eff}
            for k in range(n_tipos):
                if v1[k] > 0:
                    q = necesidad[k] / v1[k]
                    puntos.add(math.floor(q)); puntos.add(math.ceil(q))
            for p in puntos:
                if p <= 0:
                    continue
                n1 = con_min(p)
                resto = [max(0, necesidad[k] - n1 * v1[k]) for k in range(n_tipos)]
                if not any(resto):
                    continue
                if any(resto[k] > 0 and v2[k] == 0 for k in range(n_tipos)):
                    continue
                n2 = con_min(max(math.ceil(resto[k] / v2[k]) for k in range(n_tipos) if resto[k] > 0))
                probar(i, j, n1, n2, v1, v2)
        return mejor

    asignaciones = []
    c_holgura = 0.1 * pieza_media
    c_exceso = pieza_media
    relajadas = []

    def evaluar(asig):
        prod = [0] * n_tipos
        total = 0
        for patron, n in asig:
            total += n
            for k, t in enumerate(tipos_piezas):
                prod[k] += patron.get(t, 0) * n
        if any(prod[k] < necesidad[k] for k in range(n_tipos)):
            return None
        coste = total * largo_tira_objetivo
        for k in range(n_tipos):
            exc = prod[k] - necesidad[k]
            dentro = min(exc, holgura_k[k])
            coste += dentro * c_holgura + (exc - dentro) * c_exceso
        return coste

    def registrar(asig):
        asig = [(p, n) for p, n in asig if n > 0]
        if max_patrones is not None and len(asig) > max_patrones:
            return
        c = evaluar(asig)
        if c is None:
            return
        if min_efectivo and any(n < min_efectivo for _, n in asig):
            relajadas.append((c, asig))
            return
        asignaciones.append((c, asig))

    sol_12 = resolver(1)
    if sol_12 is not None:
        _, _, _, ia, ja, na, nb = sol_12
        registrar([(lista_cand[ia], na)] + ([(lista_cand[ja], nb)] if ja is not None else []))

    if len(todos) <= 3000:
        cand_multi = [c for _, c in todos]
    else:
        llenos_m = [(w, c) for w, c in todos if llena(c, w)] or todos
        elegidos_m = {clave_patron(c): c for _, c in llenos_m[:1500]}
        cand_multi = list(elegidos_m.values())
    vec_multi = [[c.get(t, 0) for t in tipos_piezas] for c in cand_multi]

    def voraz():
        falta = list(necesidad)
        usados = {}
        for _ in range(10000):
            if not any(f > 0 for f in falta):
                break
            mejor_v = None
            for idx, v in enumerate(vec_multi):
                if all(v[k] <= falta[k] for k in range(n_tipos)):
                    util = sum(v[k] * tipos_piezas[k] for k in range(n_tipos))
                    if util > 0 and (mejor_v is None or util > mejor_v[0]):
                        mejor_v = (util, idx)
            if mejor_v is not None:
                v = vec_multi[mejor_v[1]]
                n = min(falta[k] // v[k] for k in range(n_tipos) if v[k] > 0)
            else:
                for idx, v in enumerate(vec_multi):
                    util = sum(min(v[k], falta[k]) * tipos_piezas[k] for k in range(n_tipos))
                    sob = sum(max(0, v[k] - falta[k]) for k in range(n_tipos))
                    pun = util - sob * pieza_media
                    if util > 0 and (mejor_v is None or pun > mejor_v[0]):
                        mejor_v = (pun, idx)
                if mejor_v is None:
                    return None
                v = vec_multi[mejor_v[1]]
                n = 1
            if es_mitad and n % 2:
                n += 1
            n = max(1, n)
            usados[mejor_v[1]] = usados.get(mejor_v[1], 0) + n
            for k in range(n_tipos):
                falta[k] = max(0, falta[k] - v[k] * n)
        return [(cand_multi[i], n) for i, n in usados.items()]

    sol_v = voraz()
    if sol_v:
        registrar(sol_v)

    if not asignaciones and relajadas:
        asignaciones = relajadas
        aviso_min = True
    else:
        aviso_min = False

    if not asignaciones:
        return {"error": "No se encontró solución para cubrir el pedido."}

    asignaciones.sort(key=lambda x: (x[0], sum(n for _, n in x[1])))
    asignacion_final = asignaciones[0][1]

    tiras = []
    for patron, n in asignacion_final:
        for _ in range(n):
            cortes_tira = []
            for pza, cant in patron.items():
                cortes_tira.extend([pza] * cant)
            tiras.append({'cortes': cortes_tira})

    cortes_totales_fabricados = {}
    for t_ in tiras:
        for m in t_['cortes']:
            cortes_totales_fabricados[m] = cortes_totales_fabricados.get(m, 0) + 1

    grupos_tramo = {}
    for r in pedidos_proc:
        grupos_tramo.setdefault(r['m_tramo'], []).append(r)

    for m_t, lineas in grupos_tramo.items():
        fabricados = cortes_totales_fabricados.get(m_t, 0)
        necesarios = sum(r['tramos'] for r in lineas)
        sobran = max(0, fabricados - necesarios)
        for k, r in enumerate(lineas):
            fab = r['tramos'] + (sobran if k == 0 else 0)
            r['tot_piezas'] = fab * 2 if r['es_doble'] else fab
            r['demasia_actual'] = max(0, r['tot_piezas'] - r['cant_pedida'])
            r['tramos'] = fab

    conteo_patrones = {}
    for t_ in tiras:
        c_dict = {}
        for m in t_['cortes']:
            c_dict[m] = c_dict.get(m, 0) + 1
        patron_key = tuple(sorted(c_dict.items()))
        conteo_patrones[patron_key] = conteo_patrones.get(patron_key, 0) + 1

    res_patrones = []
    tiras_totales = 0
    mat_mm_totales = 0
    retal_mm = 0
    max_san_opcion = 0

    for patron_key, cant_tiras in conteo_patrones.items():
        dict_cortes = dict(patron_key)
        num_piezas = sum(dict_cortes.values())
        ocupado_piezas_kerfs = sum(m * c for m, c in dict_cortes.items()) + (num_piezas * kerf)
        espacio_disponible_util = largo_tira_objetivo - (saneamiento * 2)
        sobrante_tira = espacio_disponible_util - ocupado_piezas_kerfs
        sobrante_por_extremo = sobrante_tira / 2.0 if sobrante_tira > 0 else 0.0
        retal_mm += max(0, sobrante_tira) * cant_tiras

        cuerpo = " + ".join([f"{c}x ({int(m)} mm)" for m, c in dict_cortes.items()])
        if sobrante_por_extremo > 0:
            san_izq = saneamiento + sobrante_por_extremo
            san_der = saneamiento + sobrante_por_extremo
            if san_izq.is_integer():
                san_izq = int(san_izq); san_der = int(san_der)
            esquema_comp = f"San({san_izq}) + [{cuerpo}] + San({san_der})"
            max_san_opcion = max(max_san_opcion, float(san_izq))
        else:
            esquema_comp = f"San({saneamiento}) + [{cuerpo}] + San({saneamiento})"
            max_san_opcion = max(max_san_opcion, float(saneamiento))

        san_pat = float(san_izq) if sobrante_por_extremo > 0 else float(saneamiento)
        san_txt = str(san_izq) if sobrante_por_extremo > 0 else str(saneamiento)
        res_patrones.append({
            "cuerpo": cuerpo, "san_txt": san_txt, "tiras": cant_tiras, "tira_largo": largo_tira_objetivo,
            "esquema": esquema_comp, "san": san_pat, "nivel": nivel_saneamiento(san_pat)
        })
        tiras_totales += cant_tiras
        mat_mm_totales += (largo_tira_objetivo * cant_tiras)

    res_patrones.sort(key=lambda p_: -p_['tiras'])
    for i_, p_ in enumerate(res_patrones, 1):
        p_['num'] = i_

    piezas_utiles_mm = sum((r['cant_pedida'] + r['demasia_base']) * r['medida_base'] for r in pedidos_proc)
    piezas_de_mas_mm = sum(max(0, r['tot_piezas'] - (r['cant_pedida'] + r['demasia_base'])) * r['medida_base'] for r in pedidos_proc)
    san_mm = saneamiento * 2 * tiras_totales
    kerf_mm = max(0, mat_mm_totales - piezas_utiles_mm - piezas_de_mas_mm - san_mm - retal_mm)
    extra_u = sum(max(0, r['tot_piezas'] - (r['cant_pedida'] + r['demasia_base'])) for r in pedidos_proc)

    mat_m = mat_mm_totales / 1000.0
    aprovechado_mm = piezas_utiles_mm + piezas_de_mas_mm
    porc_aprovechamiento = (aprovechado_mm / mat_mm_totales) * 100 if mat_mm_totales > 0 else 0
    porc_desperdicio = 100 - porc_aprovechamiento if mat_mm_totales > 0 else 0

    return {
        "largo": largo_tira_objetivo, "tiras_totales": tiras_totales, "mat_m": mat_m,
        "aprovechamiento": round(porc_aprovechamiento, 1), "desperdicio": round(porc_desperdicio, 1),
        "patrones": res_patrones, "pedidos_proc": pedidos_proc, "aviso_min": aviso_min
    }