import {
  Contract,
  Footer,
  Hero,
  Nav,
  Pipeline,
  Quote,
  Results,
  States,
} from "./components/Sections";

export default function App() {
  return (
    <>
      <Nav />
      <main>
        <Hero />
        <States />
        <Pipeline />
        <Quote />
        <Results />
        <Contract />
      </main>
      <Footer />
    </>
  );
}
