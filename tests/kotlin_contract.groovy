import com.carenco.fisica.*
import groovy.json.JsonSlurper
import java.security.MessageDigest
import java.nio.ByteBuffer
import java.nio.ByteOrder
// args: JNI library, tests/fixtures, license-vectors.json (shared vectors plus keys activated for this PC).
byte[] fromHex(String s) { s.decodeHex() }
def core=new FisicaCore(args[0],{System.err.println(it)} as kotlin.jvm.functions.Function1)
def parser=new JsonSlurper()
def vectors=parser.parse(new File(args[2]))
long now=vectors.now as long
def license={ String name ->
    def context=new FisicaEntitlement(core,null,null,true,{ now } as kotlin.jvm.functions.Function0)
    assert context.importToken(vectors[name])==FisicaEntitlementOutcome.ACCEPTED:name
    context
}
parser.parse(new File(args[1],'sessions.json')).each { v ->
    def session=new FisicaSession(core)
    try {
        v.steps.each { step ->
            long time=step.now_ms
            if(step.containsKey('action')) session.action(step.action as int,step.value as long,time)
            else if(step.containsKey('feed_hex')) session.feed(fromHex(step.feed_hex),time)
            else if(step.containsKey('command')) session.command(step.command,0d,new byte[0],time)
            else session.tick(time)
            def actual=session.drain().collect { e -> [(0..<5).collect { e.metadata[it] },e.payload.encodeHex().toString()].flatten() }
            assert actual==step.events:v.id
        }
    } finally { session.close() }
}
// Decoding a packet yourself hands out raw and processed grids: full mode with raw data only.
def full=license('full'), limited=license('limited')
assert full.status().mode==FisicaMode.FULL && full.hasFeature(FisicaFeature.RAW_DATA)
def legacy=parser.parse(new File(args[1],'legacy-processing.json'))
legacy.each { v ->
    def m=core.decode(fromHex(v.packet_hex),full);assert m.weight==v.weight && m.raw.length==638 && m.packet.length==645
    def data=ByteBuffer.allocate(m.display.length*8).order(ByteOrder.LITTLE_ENDIAN)
    m.display.each { data.putDouble(it) }
    assert MessageDigest.getInstance('SHA-256').digest(data.array()).encodeHex().toString()==v.display_sha256
}
def synthetic=legacy.find { it.id=='synthetic-recorded-layout' }, packet=fromHex(synthetic.packet_hex)
assert limited.status().features==(FisicaFeature.EXPORT|FisicaFeature.FULL_RATE)  // a key's features narrow full mode
[null,limited].each { context ->
    try { core.decode(packet,context); assert false:'decode without raw data must fail' }
    catch (FisicaLicenseException e) { assert e.code==FisicaStatus.FEATURE_NOT_LICENSED && e instanceof FisicaException }
}
full.close();limited.close()
// Analysis needs no license: weight and foot size are basic-mode results.
def grid=new double[841];grid[4*29+3]=grid[4*29+20]=100
def matrices=new double[841*12];for(int i=0;i<12;i++) System.arraycopy(grid,0,matrices,i*841,841)
def weights=new double[12];Arrays.fill(weights,70.5d)
assert core.analyze(matrices,weights,29,29).toList()==[70.5d,1d,1d,1d,1d]
assert core.analyzePackets([packet]*12)[0]==synthetic.weight
println 'Kotlin/JNI shared session, processing and license-gating vectors passed'
