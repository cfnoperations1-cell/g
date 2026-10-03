"""Seed outreach/sent_log.csv from the addresses already emailed from jonathan@marinexisbiologics.com
(captured from the Gmail Sent folder). This is the dedup source of truth for every draft/send batch."""
import csv, os
SENT = """
info@houstonwellnesscenter.org hello@thegoodlifeiv.com info@next-health.com dr.cruz@orlandocityhealth.com
mark@vipweightlosscenters.com info@ethoswellnessok.com consults@promdhealth.com info@polarisrejuvenation.com
info@bostonvitality.com hello@regenerationutah.com hello@meetingpointhealth.com info@abluxurymedspa.com
info@ivelements.net info@theflowwellness.com info@infiniteyouthmedical.com info@monacohealth.net
info@totalbodywellness.com info@glossaesthetics.com info@slmmedspa.com hello@bettermedspa.com
info@limedicalaesthetics.com office@drsende.com info@regenesismd.com tracy@rejuv-kc.com info@stronghealth.com
info@behuemn.com info@drtonivarela.com partners@noom.com office@juventaswellness.com
support@limitlessbiochem.eu info@pinnaclepeptidelabs.com cs@omegamino.net sales@livewellpeptides.com
support@elevatedpeptides.com support@somapeptides.com support@elyriabio.com support@purepeptidelabs.shop
support@chemyo.com support@solyn.com support@valorpeptides.com sales@zenaminos.com support@xenopeptides.com
support@ironlabs.shop info@peptidegroupbuy.com info@elitebiogenix.com admin@safewellnesscenter.org cs@pepkits.shop
support@nobledragons.com support@goldeneagledistributors.com support@myoasislabs.com customersupport@coffeeandpeppers.com
polypeptide869@163.com luxsynthaminos@gmail.com support@peptidehubs.com info@revivpeptides.com
support@aminocoreresearch.com charles@newbeginningpeps.com orders@mainpeptides.com regenwellph@gmail.com
support@soliralab.com info@atlaspeptidelab.com contact@ehzpeptides.com cs@moglabs.bio support@puritypeptides.is
rhomepeptides@gmail.com support@vincerevitae.com service@novabioresearchlab.com admin@revolutionpeptide.com
info@bioedgeresearchlabs.com info@bizpeptide.com cs@amc-essentials.com aavant@peptide.email support@getmypepti.com
support@purelabpeptides.com sales@geopeptides.com info@fusionpeptide.com hello@aiopeptides.com hello@trulabpeptides.com
orders@warrior-makers.com doublerlabs@protonmail.com admin@gxresearch.shop support@hyteresearchdivision.com
sales@medchemexpress.com support@alphaomegapeptide.com store@apeiron.store support@wellnessresearchsupply.com
support@alphabiomedlabs.com info@ascendpeptidesuk.com support@alphacarbonlabs.com cs@alpha-peptides.com
support@biosynergen.us support@thepeptidesourcellc.com orders@eliteresearchusa.com info@exceedenhancement.com
info@glacieraminos.shop support@ironwithin.io support@peptidesupplyco.com support@longevitypeptides.us
support@maddogpeps.com support@trueformpeptides.com support@vantageaminos.co info@eliteedgebiotech.com
contact@genpeptide.com info@particlepeptides.com support@peaklabpeptides.com support@pepxlab.com
info@premierbiolabs.com info@purelabperformance.com info@reliablepeptides.com support@aeylabs.org info@biomaxresearch.com
support@crownwellresearch.com alexia7438@outlook.com support@horizonlabsbio.com support@olympexsolutions.com
info@peptideruo.com sales@acelabs.pro support@myprojectzero.com customerservice@wellilabs.com
dankangpeptidesfactory@outlook.com info@peptidegurus.com info@suaway.com info@vorilabs.com support@longevityplan.ai
peptaverse@sudomail.com research@peptidecraft.co sales@innmi.com support@optimumcompounds.com
support@sciencebasedpeptides.com support@pinnaclebiosupply.com support@aminonova.com info@03peptides.com
admin@xtxinyao.cn cs@aminosequence.com support@arizenbiolabs.com consult@deltavalleyco.com support@happypeptides.com
support@onedaycompounds.com support@pepsgpt.com support@pythionresearch.com cs@arcanepeptides.com vicky@qhpeptides.com
info@prettypeptide.co.uk nexbiopeptix@gmail.com info@lapeptides.net zepeptide@outlook.com support@peptaura.com
support@compoundpurity.com hello@tryaurapeptides.com info@orderignite.com support@bioinfinity.co support@peptidemaxxing.com
support@stemcodepeptides.com support@powerpepgroup.com hello@proxivalabs.com hello@prohelixflorida.com
support@alphapeps.com support@echopeptides.co support@mistertide.com info@nootropicsource.com
support@sfbayareapeptides.com irene@aspnlabs.org info@modern-peptides.com support@masterspeptides.com
support@peptidedirect.com info@rejuvenatepeptides.com support@crestbiolabs.com sales@lummisnova.com
info@sentinelvalor.com support@zerovariancelabs.com support@bourbonpeptides.com support@capstonepeptides.com
support@lumexhealth.com help@rivnresearch.com marlon@b12injectionsmiami.com support@sqspeptides.com 17513060195@163.com
support@nexusbiolife.com support@novoprolabs.com support@optimumformula.co support@pepperlicious.com
support@peptidesxpress.com contact@vesaliuslabs.com support@researchchemhq.co support@solidpeptides.com
cs@southernaminos.com support@studzpeptides.com thejester@thekitqueens.com support@synbio-tech.com javik@thepeplair.com
support@triumphantlabs.com support@valapeptides.com cs.primalchainresearchllc@gmail.com redcapelabs@proton.me
info@excaliburpeptides.com contact@spartanbiolab.com support@vitalityproject.global vivpeptide@gmail.com
support@voidresearch.shop sales@aurobiopeptide.com support@flatironsrc.com godbiolab@pm.me support@peakformpeptides.com
affordablepeptidesorders@gmail.com gzhaiyitong@outlook.com contact@vayn.co rene.lozano0591@gmail.com
contact@maximumpeps.com support@maplelabs.store support@prettypeptidecollective.com info@superpeptides.com
support@synergybiopeps.com baiwei@usprimeway.com renovapeptides@gmail.com landgb39@gmail.com info@tritideresearch.com
support@volterasciences.com info@zerogravitynky.com support@5chainzlabs.com support@absolutepep.com
support@alphaeliteshop.com info@americanpeptideco.com aminoelementslab@gmail.com support@aminoxgen.com
dzseszikajerbe@gmail.com support@ascendpeptidesusa.com alex.barn001@gmail.com support@bioxcell.com
service@biofusionoutlet.com hello@bluumpeptides.com bulktides@gmail.com info@calpeps.com thelobster@tuta.com
info@chemexpress.com contact@discoveryresearchusa.com info@easypeptide.com help@enhancedexecutive.com
support@explicitresearch.com sales1@faithfulbio.com support@hightidecompounds.com support@injectify.is
support@ironaminos.com info@jitaibiotech.com info@jpt.com lorenna95@hotmail.com info@mammothmusclepeptides.com
josh@milehighpeptide.com empirelabsca@gmail.com info@eliteeons.com info@regena-peptides.com
"""
MEDSPA = {"info@houstonwellnesscenter.org","hello@thegoodlifeiv.com","info@next-health.com","dr.cruz@orlandocityhealth.com",
 "mark@vipweightlosscenters.com","info@ethoswellnessok.com","consults@promdhealth.com","info@polarisrejuvenation.com",
 "info@bostonvitality.com","hello@regenerationutah.com","hello@meetingpointhealth.com","info@abluxurymedspa.com",
 "info@ivelements.net","info@theflowwellness.com","info@infiniteyouthmedical.com","info@monacohealth.net",
 "info@totalbodywellness.com","info@glossaesthetics.com","info@slmmedspa.com","hello@bettermedspa.com",
 "info@limedicalaesthetics.com","office@drsende.com","info@regenesismd.com","tracy@rejuv-kc.com","info@stronghealth.com",
 "info@behuemn.com","info@drtonivarela.com","office@juventaswellness.com"}
emails = [e.strip().lower() for e in SENT.split() if "@" in e]
seen, rows = set(), []
for e in emails:
    if e in seen: continue
    seen.add(e)
    rows.append({"email": e, "domain": e.split("@")[1], "audience": "medspa" if e in MEDSPA else "vendor",
                 "mode": "sent", "via": "jonathan@marinexisbiologics.com"})
os.makedirs("outreach", exist_ok=True)
with open("outreach/sent_log.csv","w",newline="",encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["email","domain","audience","mode","via"]); w.writeheader(); w.writerows(rows)
print(f"sent_log.csv: {len(rows)} already-emailed addresses ({sum(1 for r in rows if r['audience']=='medspa')} medspa, {sum(1 for r in rows if r['audience']=='vendor')} vendor)")
